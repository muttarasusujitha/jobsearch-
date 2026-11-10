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

-- Companies the startup finder has checked. ats/slug are NULL when no public board was found.
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'seed',
    ats TEXT,
    slug TEXT,
    open_jobs INTEGER NOT NULL DEFAULT 0,
    local_jobs INTEGER NOT NULL DEFAULT 0,
    checked_at TEXT NOT NULL
);

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


def company_key(name: str) -> str:
    """Normalised company name used to de-duplicate ('Razorpay Software Pvt Ltd' -> 'razorpay software')."""
    import re

    name = re.sub(r"\b(private|pvt|ltd|limited|inc|llc|llp|technologies|technology|labs|india)\b\.?", " ", name.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", name).split())


def upsert_company(conn: sqlite3.Connection, c: dict) -> None:
    key = company_key(c["name"])
    if not key:
        return
    conn.execute(
        """INSERT INTO companies (name, name_key, city, source, ats, slug, open_jobs, local_jobs, checked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(name_key) DO UPDATE SET
             city = CASE WHEN excluded.city != '' THEN excluded.city ELSE companies.city END,
             ats = excluded.ats, slug = excluded.slug, open_jobs = excluded.open_jobs,
             local_jobs = excluded.local_jobs, checked_at = excluded.checked_at""",
        (c["name"], key, c.get("city", ""), c.get("source", "seed"), c.get("ats"), c.get("slug"),
         int(c.get("open_jobs", 0)), int(c.get("local_jobs", 0)), c.get("checked_at") or now_iso()),
    )
    conn.commit()


def companies(conn: sqlite3.Connection, found_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM companies"
    if found_only:
        sql += " WHERE ats IS NOT NULL"
    return conn.execute(sql + " ORDER BY local_jobs DESC, name").fetchall()


def recently_checked(conn: sqlite3.Connection, days: int = 14) -> set[str]:
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return {r[0] for r in conn.execute("SELECT name_key FROM companies WHERE checked_at >= ?", (since,))}


def company_candidates(conn: sqlite3.Connection, cities: list[str], limit: int = 200) -> list[dict]:
    """Companies seen in job postings located in `cities` that the finder hasn't checked recently."""
    from .matching import city_of

    skip = recently_checked(conn)
    seen: dict[str, dict] = {}
    for row in conn.execute("SELECT company, location FROM jobs WHERE company != '' ORDER BY id DESC LIMIT 5000"):
        city = city_of(row["location"])
        if not city or (cities and city not in cities):
            continue
        key = company_key(row["company"])
        if key and key not in skip and key not in seen:
            seen[key] = {"name": row["company"], "city": city.title(), "source": "jobs"}
        if len(seen) >= limit:
            break
    return list(seen.values())
