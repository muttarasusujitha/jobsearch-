"""SQLite storage for jobs, their status history, and processed emails."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH
from .models import JobPosting

# Pipeline order, shown in this order on the dashboard.
STATUSES = [
    "discovered",       # found by the discovery agent
    "matched",          # passed matching, waiting for your approval (review mode)
    "approved",         # ready to apply
    "skipped",          # failed matching, or you skipped it
    "needs_attention",  # agent couldn't finish (CAPTCHA, unknown question, external site)
    "failed",           # unexpected error
    "applied",
    "viewed",
    "shortlisted",
    "interview",
    "offer",
    "rejected",
]

# Statuses the email tracker can move a job through. It only moves forward.
TRACKING_RANK = {"applied": 0, "viewed": 1, "shortlisted": 2, "interview": 3, "offer": 4}
POST_APPLY = set(TRACKING_RANK) | {"rejected"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    apply_url TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    posted_at TEXT,
    discovered_at TEXT NOT NULL,
    score REAL,
    match_reasons TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    status_updated_at TEXT NOT NULL,
    applied_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE (platform, external_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_applied_at ON jobs (applied_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events (job_id);

CREATE TABLE IF NOT EXISTS processed_emails (
    message_id TEXT PRIMARY KEY,
    job_id INTEGER REFERENCES jobs (id) ON DELETE SET NULL,
    classification TEXT,
    subject TEXT,
    sender TEXT,
    received_at TEXT,
    processed_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_of_local_day_utc() -> str:
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_job(conn: sqlite3.Connection, job: JobPosting) -> tuple[int, bool]:
    """Insert a new job. Returns (job_id, created). Existing jobs keep their status."""
    row = conn.execute(
        "SELECT id FROM jobs WHERE platform = ? AND external_id = ?",
        (job.platform, job.external_id),
    ).fetchone()
    if row:
        # Fill in details that may have been missing the first time.
        conn.execute(
            """UPDATE jobs SET
                 description = CASE WHEN description = '' THEN ? ELSE description END,
                 company = CASE WHEN company = '' THEN ? ELSE company END,
                 location = CASE WHEN location = '' THEN ? ELSE location END
               WHERE id = ?""",
            (job.description, job.company, job.location, row["id"]),
        )
        conn.commit()
        return row["id"], False

    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO jobs (platform, external_id, url, apply_url, title, company, location,
                             description, posted_at, discovered_at, status, status_updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)""",
        (
            job.platform, job.external_id, job.url, job.apply_url, job.title, job.company,
            job.location, job.description, job.posted_at, ts, ts,
        ),
    )
    job_id = int(cur.lastrowid or 0)
    conn.execute(
        "INSERT INTO events (job_id, status, note, created_at) VALUES (?, 'discovered', '', ?)",
        (job_id, ts),
    )
    conn.commit()
    return job_id, True


def set_status(conn: sqlite3.Connection, job_id: int, status: str, note: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    ts = now_iso()
    if status == "applied":
        conn.execute(
            "UPDATE jobs SET status = ?, status_updated_at = ?, applied_at = COALESCE(applied_at, ?) WHERE id = ?",
            (status, ts, ts, job_id),
        )
    else:
        conn.execute(
            "UPDATE jobs SET status = ?, status_updated_at = ? WHERE id = ?", (status, ts, job_id)
        )
    conn.execute(
        "INSERT INTO events (job_id, status, note, created_at) VALUES (?, ?, ?, ?)",
        (job_id, status, note, ts),
    )
    conn.commit()


def is_forward_move(current: str, new: str) -> bool:
    """True if the email tracker is allowed to move a job from `current` to `new`."""
    if current not in POST_APPLY or current in ("offer", "rejected"):
        return False
    if new == "rejected":
        return True
    return TRACKING_RANK.get(new, -1) > TRACKING_RANK.get(current, -1)


def jobs_with_status(conn: sqlite3.Connection, *statuses: str) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(statuses))
    return conn.execute(
        f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY score DESC, id", statuses
    ).fetchall()


def applied_count_since(conn: sqlite3.Connection, since_iso: str, platform: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM jobs WHERE applied_at >= ?"
    params: list[str] = [since_iso]
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    return int(conn.execute(sql, params).fetchone()[0])


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {s: 0 for s in STATUSES}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts
