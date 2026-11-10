"""Application agent: applies to approved jobs within your daily limits."""

from __future__ import annotations

import logging
import random
import time
from collections import defaultdict

from .. import db
from ..answers import Answerer, render_cover_letter
from ..browser import BrowserSession
from ..config import Settings
from ..platforms import PLATFORMS
from .matcher import match_job

log = logging.getLogger(__name__)


def run_applications(
    settings: Settings,
    platforms: list[str] | None = None,
    headless: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    conn = db.connect()
    limits = settings.limits
    daily_max = int(limits.get("daily_max", 25))
    per_platform_max = int(limits.get("per_platform_daily_max", 10))
    min_delay = float(limits.get("min_delay_seconds", 25))
    max_delay = float(limits.get("max_delay_seconds", 70))
    since = db.start_of_local_day_utc()

    enabled = set(platforms or settings.enabled_platforms)
    by_platform = defaultdict(list)
    for row in db.jobs_with_status(conn, "approved"):
        if row["platform"] in enabled and row["platform"] in PLATFORMS:
            by_platform[row["platform"]].append(row)

    stats: dict[str, int] = defaultdict(int)
    remaining_today = daily_max - db.applied_count_since(conn, since)
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
                    db.set_status(conn, row["id"], result.status, result.note)
                stats["manual"] += 1
            continue
        platform_left = per_platform_max - db.applied_count_since(conn, since, name)
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
                    if len(row["description"] or "") < 300:
                        description = adapter.fetch_description(page, row)
                        if description:
                            conn.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, row["id"]))
                            conn.commit()
                            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
                            if not match_job(conn, settings, row):
                                stats["skipped_after_details"] += 1
                                continue
                            if settings.review_mode:
                                # You already approved it; keep it approved after re-scoring.
                                db.set_status(conn, row["id"], "approved", "Re-checked with full description")
                            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()

                    answerer = Answerer(settings, render_cover_letter(settings, row["title"], row["company"]))
                    result = adapter.apply(page, row, answerer, dry_run=dry_run)
                except Exception as exc:
                    log.exception("Error applying to job %s", row["id"])
                    result_status, note = "failed", f"{type(exc).__name__}: {exc}"[:300]
                else:
                    result_status, note = result.status, result.note

                attempted += 1
                stats[result_status] += 1
                if result_status == "dry_run":
                    log.info("  dry run: %s", note)
                    db.set_status(conn, row["id"], "approved", f"Dry run: {note}")
                else:
                    db.set_status(conn, row["id"], result_status, note)
                    log.info("  -> %s %s", result_status, note)
                if result_status == "applied":
                    platform_left -= 1
                time.sleep(random.uniform(min_delay, max_delay) if not dry_run else 2)
    conn.close()
    return dict(stats)
