"""The API service owns the database. These functions are its event handlers and queries.

They have no FastAPI dependency so they can be tested with a MemoryBus and a temp database.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from jobagent import db, events
from jobagent.agents.tracker import process_message
from jobagent.bus import Bus
from jobagent.config import Settings
from jobagent.models import JobPosting

log = logging.getLogger(__name__)

JOB_COLUMNS = (
    "id, platform, external_id, title, company, location, score, status, match_reasons, url, apply_url, "
    "description, discovered_at, applied_at, status_updated_at"
)
API_STREAMS = [
    events.JOB_DISCOVERED,
    events.JOB_SCORED,
    events.JOB_DESCRIPTION,
    events.JOB_APPLY_RESULT,
    events.EMAIL_RECEIVED,
    events.COMPANY_CHECKED,
]
POSTING_FIELDS = set(JobPosting.__dataclass_fields__)


class ApiLogic:
    def __init__(self, conn: sqlite3.Connection, bus: Bus, settings: Settings | None):
        self.conn = conn
        self.bus = bus
        self.settings = settings

    # ------------------------------------------------------------ events in
    def handle(self, stream: str, payload: dict[str, Any]) -> None:
        handler = {
            events.JOB_DISCOVERED: self.on_discovered,
            events.JOB_SCORED: self.on_scored,
            events.JOB_DESCRIPTION: self.on_description,
            events.JOB_APPLY_RESULT: self.on_apply_result,
            events.EMAIL_RECEIVED: self.on_email,
            events.COMPANY_CHECKED: self.on_company,
        }.get(stream)
        if handler:
            handler(payload)

    def on_discovered(self, p: dict[str, Any]) -> None:
        posting = JobPosting(**{k: v for k, v in p.items() if k in POSTING_FIELDS})
        job_id, created = db.upsert_job(self.conn, posting)
        if created:
            self.bus.publish(events.JOB_NEW, self.job(job_id) or {})

    def on_scored(self, p: dict[str, Any]) -> None:
        row = self.conn.execute("SELECT status FROM jobs WHERE id = ?", (p["id"],)).fetchone()
        if not row or row["status"] != "discovered":
            return  # already handled (messages can be delivered more than once)
        reasons = p.get("reasons") or []
        self.conn.execute(
            "UPDATE jobs SET score = ?, match_reasons = ? WHERE id = ?", (p.get("score"), " | ".join(reasons), p["id"])
        )
        if not p.get("passed"):
            db.set_status(self.conn, p["id"], "skipped", reasons[-1] if reasons else "")
            return
        review = self.settings.review_mode if self.settings else True
        db.set_status(self.conn, p["id"], "matched" if review else "approved", f"Score {p.get('score')}")

    def on_description(self, p: dict[str, Any]) -> None:
        self.conn.execute("UPDATE jobs SET description = ? WHERE id = ?", (p.get("description", ""), p["id"]))
        self.conn.commit()

    def on_apply_result(self, p: dict[str, Any]) -> None:
        status, note = p.get("status", "failed"), p.get("note", "")
        if status == "dry_run":
            db.set_status(self.conn, p["id"], "approved", f"Dry run: {note}")
        elif status in db.STATUSES:
            db.set_status(self.conn, p["id"], status, note)

    def on_email(self, p: dict[str, Any]) -> None:
        new = process_message(
            self.conn, p["message_id"], p.get("subject", ""), p.get("sender", ""), p.get("body", ""), p.get("received", "")
        )
        if new:
            log.info("Email '%s' -> %s", p.get("subject", "")[:80], new)

    def on_company(self, p: dict[str, Any]) -> None:
        db.upsert_company(self.conn, p)

    # ------------------------------------------------------------ queries
    def job(self, job_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def jobs(self, status: list[str] | None = None, platform: str | None = None, q: str | None = None,
             limit: int = 5000, with_description: bool = False) -> list[dict[str, Any]]:
        cols = JOB_COLUMNS if with_description else JOB_COLUMNS.replace("description, ", "")
        sql, params = f"SELECT {cols} FROM jobs WHERE 1=1", []
        if status:
            sql += f" AND status IN ({','.join('?' * len(status))})"
            params += status
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        if q:
            sql += " AND (title LIKE ? OR company LIKE ? OR location LIKE ?)"
            params += [f"%{q}%"] * 3
        sql += " ORDER BY status_updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 20000)))
        return [dict(r) for r in self.conn.execute(sql, params)]

    def set_status(self, job_id: int, status: str, note: str) -> dict[str, Any]:
        if status not in db.STATUSES:
            raise ValueError(f"Unknown status {status!r}")
        if not self.job(job_id):
            raise KeyError(job_id)
        db.set_status(self.conn, job_id, status, note)
        return self.job(job_id) or {}

    def events(self, limit: int = 300, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT e.created_at, e.status, e.note, e.job_id, j.platform, j.title, j.company FROM events e "
               "JOIN jobs j ON j.id = e.job_id")
        params: list[Any] = []
        if statuses:
            sql += f" WHERE e.status IN ({','.join('?' * len(statuses))})"
            params += statuses
        sql += " ORDER BY e.id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 5000)))
        return [dict(r) for r in self.conn.execute(sql, params)]

    def last_notes(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT job_id, note FROM events WHERE id IN (SELECT MAX(id) FROM events WHERE job_id IN ({marks}) GROUP BY job_id)",
            ids,
        )
        return {r["job_id"]: r["note"] for r in rows}

    def applied_count(self, since: str, platform: str | None = None) -> int:
        return db.applied_count_since(self.conn, since, platform)

    def last_activity(self) -> str | None:
        return self.conn.execute("SELECT MAX(created_at) FROM events").fetchone()[0]

    def emails(self, limit: int = 200) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT received_at, classification, subject, sender, job_id FROM processed_emails "
            "WHERE classification IS NOT NULL ORDER BY received_at DESC LIMIT ?", (limit,))]

    def companies(self, found_only: bool = False) -> list[dict[str, Any]]:
        return [dict(r) for r in db.companies(self.conn, found_only)]

    # ------------------------------------------------------------ commands out
    def run(self, agent: str, args: dict[str, Any]) -> str:
        if agent == "rescore":
            rows = db.jobs_with_status(self.conn, "matched", "skipped")
            for r in rows:
                self.conn.execute("UPDATE jobs SET status = 'discovered' WHERE id = ?", (r["id"],))
            self.conn.commit()
            for r in rows:
                self.bus.publish(events.JOB_NEW, self.job(r["id"]) or {})
            return f"Re-scoring {len(rows)} jobs"
        stream = events.COMMANDS.get(agent)
        if not stream:
            raise KeyError(agent)
        allowed = {"platforms", "dry_run", "limit", "cities"}
        self.bus.publish(stream, {k: v for k, v in args.items() if k in allowed})
        return f"Started {agent}"
