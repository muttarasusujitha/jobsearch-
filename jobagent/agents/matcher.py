"""Matching agent: scores discovered jobs and routes them."""

from __future__ import annotations

import logging
import sqlite3

from .. import db
from ..config import Settings
from ..matching import score_job
from ..models import JobPosting

log = logging.getLogger(__name__)


def row_to_posting(row: sqlite3.Row) -> JobPosting:
    return JobPosting(
        platform=row["platform"],
        external_id=row["external_id"],
        url=row["url"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        description=row["description"],
        apply_url=row["apply_url"],
    )


def match_job(conn: sqlite3.Connection, settings: Settings, row: sqlite3.Row) -> bool:
    """Score one job and set it to matched/approved/skipped. Returns True if it passed."""
    result = score_job(row_to_posting(row), settings.preferences)
    conn.execute(
        "UPDATE jobs SET score = ?, match_reasons = ? WHERE id = ?",
        (result.score, " | ".join(result.reasons), row["id"]),
    )
    if not result.passed:
        db.set_status(conn, row["id"], "skipped", result.reasons[-1] if result.reasons else "")
        return False
    next_status = "matched" if settings.review_mode else "approved"
    db.set_status(conn, row["id"], next_status, f"Score {result.score}")
    return True


def match_pending(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    stats = {"matched": 0, "skipped": 0}
    for row in db.jobs_with_status(conn, "discovered"):
        if match_job(conn, settings, row):
            stats["matched"] += 1
        else:
            stats["skipped"] += 1
    log.info("Matching: %d passed, %d skipped", stats["matched"], stats["skipped"])
    return stats


def rescore_all(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    """Re-run matching on matched/skipped jobs after you change your preferences."""
    rows = db.jobs_with_status(conn, "matched", "skipped")
    for row in rows:
        conn.execute("UPDATE jobs SET status = 'discovered' WHERE id = ?", (row["id"],))
    conn.commit()
    return match_pending(conn, settings)
