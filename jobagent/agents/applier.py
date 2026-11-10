"""Application agent: applies to approved jobs within your daily limits.

The agent reads and writes through a small "store" so the same code works in CLI mode
(DbStore, the local SQLite file) and in the microservices (ServiceStore, the API + bus).
"""

from __future__ import annotations

import logging
import random
import time
from collections import defaultdict
from typing import Any, Protocol

from .. import db
from ..answers import Answerer, render_cover_letter
from ..browser import BrowserSession
from ..config import Settings
from ..matching import score_job
from ..platforms import PLATFORMS
from .matcher import row_to_posting

log = logging.getLogger(__name__)


class ApplyStore(Protocol):
    def approved_jobs(self) -> list[dict[str, Any]]: ...
    def applied_count(self, since: str, platform: str | None = None) -> int: ...
    def save_description(self, job_id: int, description: str) -> None: ...
    def set_result(self, job_id: int, status: str, note: str) -> None: ...


class DbStore:
    def __init__(self) -> None:
        self.conn = db.connect()

    def approved_jobs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in db.jobs_with_status(self.conn, "approved")]

    def applied_count(self, since: str, platform: str | None = None) -> int:
        return db.applied_count_since(self.conn, since, platform)

    def save_description(self, job_id: int, description: str) -> None:
        self.conn.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))
        self.conn.commit()

    def set_result(self, job_id: int, status: str, note: str) -> None:
        if status == "dry_run":
            db.set_status(self.conn, job_id, "approved", f"Dry run: {note}")
        else:
            db.set_status(self.conn, job_id, status, note)


def run_applications(
    settings: Settings,
    platforms: list[str] | None = None,
    headless: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    store: ApplyStore | None = None,
    sleep: Any = time.sleep,
) -> dict[str, int]:
    store = store or DbStore()
    limits = settings.limits
    daily_max = int(limits.get("daily_max", 25))
    per_platform_max = int(limits.get("per_platform_daily_max", 10))
    min_delay = float(limits.get("min_delay_seconds", 25))
    max_delay = float(limits.get("max_delay_seconds", 70))
    since = db.start_of_local_day_utc()

    enabled = set(platforms or settings.enabled_platforms)
    by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in store.approved_jobs():
        if row["platform"] in enabled and row["platform"] in PLATFORMS:
            by_platform[row["platform"]].append(row)

    stats: dict[str, int] = defaultdict(int)
    remaining_today = daily_max - store.applied_count(since)
    if limit is not None:
        remaining_today = min(remaining_today, limit)
    waiting = sum(len(rows) for rows in by_platform.values())
    if remaining_today <= 0:
        log.info("Daily limit of %d reached. %d approved jobs wait until tomorrow.", daily_max, waiting)
        stats["waiting_daily_limit"] = waiting
        return dict(stats)
    if not waiting:
        log.info("No approved jobs to apply to on: %s", ", ".join(sorted(enabled)))

    attempted = 0
    for name, rows in by_platform.items():
        adapter = PLATFORMS[name](settings, headless=headless)
        if adapter.manual_apply:
            # No browser needed: hand these to you in "Needs attention" with the link.
            for row in rows:
                result = adapter.apply(None, row, Answerer(settings), dry_run=dry_run)  # type: ignore[arg-type]
                if not dry_run:
                    store.set_result(row["id"], result.status, result.note)
                stats["manual"] += 1
            continue
        platform_left = per_platform_max - store.applied_count(since, name)
        if platform_left <= 0:
            log.info("%s: per-platform daily limit reached", name)
            stats[f"waiting_{name}_daily_limit"] += len(rows)
            continue
        with BrowserSession(adapter.profile, headless=headless) as session:
            page = session.new_page()
            if adapter.login_url and not adapter.is_logged_in(page):
                log.error(
                    "%s: not logged in, so %d approved jobs were skipped. Run: python -m jobagent login %s",
                    name, len(rows), name,
                )
                stats[f"skipped_{name}_not_logged_in"] += len(rows)
                continue
            for row in rows:
                if attempted >= remaining_today or platform_left <= 0:
                    break
                log.info("Applying: [%s] %s at %s", name, row["title"], row["company"])
                try:
                    # Search cards have little text; fetch the full description and re-check the match.
                    if len(row.get("description") or "") < 300:
                        description = adapter.fetch_description(page, row)  # type: ignore[arg-type]
                        if description:
                            store.save_description(row["id"], description)
                            row = {**row, "description": description}
                            check = score_job(row_to_posting(row), settings.preferences)
                            if not check.passed:
                                store.set_result(row["id"], "skipped", check.reasons[-1] if check.reasons else "No longer matches")
                                stats["skipped_after_details"] += 1
                                continue

                    answerer = Answerer(settings, render_cover_letter(settings, row["title"], row["company"]))
                    result = adapter.apply(page, row, answerer, dry_run=dry_run)  # type: ignore[arg-type]
                except Exception as exc:
                    log.exception("Error applying to job %s", row["id"])
                    result_status, note = "failed", f"{type(exc).__name__}: {exc}"[:300]
                else:
                    result_status, note = result.status, result.note

                attempted += 1
                stats[result_status] += 1
                store.set_result(row["id"], result_status, note)
                log.info("  -> %s %s", result_status, note)
                if result_status == "applied":
                    platform_left -= 1
                sleep(random.uniform(min_delay, max_delay) if not dry_run else 2)
    return dict(stats)
