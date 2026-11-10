"""Where the dashboard gets its data.

- LocalBackend: reads the SQLite file directly and starts agents as CLI subprocesses
  (python -m jobagent dashboard).
- ApiBackend: talks to the API service (docker compose). Used when JOBAGENT_API_URL is set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from jobagent import db
from jobagent.apiclient import ApiClient
from jobagent.config import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = DATA_DIR / "agent.log"
JOB_COLS = ("id, platform, title, company, location, score, status, match_reasons, url, "
            "discovered_at, applied_at, status_updated_at")


class LocalBackend:
    mode = "local"

    def __init__(self) -> None:
        self.conn = db.connect()

    def jobs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(f"SELECT {JOB_COLS} FROM jobs ORDER BY status_updated_at DESC")]

    def events(self, statuses: list[str], limit: int) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(statuses))
        return [dict(r) for r in self.conn.execute(
            "SELECT e.created_at, e.status, e.note, j.platform, j.title, j.company FROM events e JOIN jobs j ON j.id = e.job_id "
            f"WHERE e.status IN ({marks}) ORDER BY e.id DESC LIMIT ?", [*statuses, limit])]

    def last_notes(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT job_id, note FROM events WHERE id IN (SELECT MAX(id) FROM events WHERE job_id IN ({marks}) GROUP BY job_id)", ids)
        return {r["job_id"]: r["note"] for r in rows}

    def last_activity(self) -> str | None:
        return self.conn.execute("SELECT MAX(created_at) FROM events").fetchone()[0]

    def companies(self) -> list[dict[str, Any]]:
        return [dict(r) for r in db.companies(self.conn)]

    def set_status(self, job_id: int, status: str, note: str) -> None:
        db.set_status(self.conn, job_id, status, note)

    def run(self, agent: str, dry_run: bool = False, limit: int | None = None) -> str:
        cli = {"discover": ["discover"], "apply": ["apply"], "track": ["track"],
               "find_startups": ["find-startups"], "rescore": ["match", "--rescore"]}[agent]
        if dry_run:
            cli.append("--dry-run")
        if limit:
            cli += ["--limit", str(limit)]
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            subprocess.Popen([sys.executable, "-m", "jobagent", *cli], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
        return f"Started: {' '.join(cli)}"

    def log_tail(self, n: int = 40) -> str | None:
        if not LOG_FILE.exists():
            return None
        return "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])


class ApiBackend:
    mode = "services"

    def __init__(self) -> None:
        self.api = ApiClient()

    def jobs(self) -> list[dict[str, Any]]:
        return self.api.get("/jobs", limit=20000)

    def events(self, statuses: list[str], limit: int) -> list[dict[str, Any]]:
        return self.api.get("/events", status=statuses, limit=limit)

    def last_notes(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        return {int(k): v for k, v in self.api.get("/notes", ids=ids).items()}

    def last_activity(self) -> str | None:
        return self.api.get("/stats/last-activity").get("at")

    def companies(self) -> list[dict[str, Any]]:
        return self.api.get("/companies")

    def set_status(self, job_id: int, status: str, note: str) -> None:
        self.api.post(f"/jobs/{job_id}/status", {"status": status, "note": note})

    def run(self, agent: str, dry_run: bool = False, limit: int | None = None) -> str:
        body: dict[str, Any] = {"dry_run": dry_run}
        if limit:
            body["limit"] = limit
        return self.api.post(f"/runs/{agent}", body)["message"]

    def log_tail(self, n: int = 40) -> str | None:
        return None  # each service logs to its container: docker compose logs -f


def get_backend() -> LocalBackend | ApiBackend:
    return ApiBackend() if os.environ.get("JOBAGENT_API_URL") else LocalBackend()
