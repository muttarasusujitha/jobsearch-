"""Discovery agent: collects job openings from every enabled platform."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Callable, Iterator

from .. import db
from ..browser import BrowserSession
from ..config import Settings
from ..models import JobPosting
from ..platforms import PLATFORMS
from ..startups import merge_registry
from .matcher import match_pending

log = logging.getLogger(__name__)


def discover_postings(
    settings: Settings,
    platforms: list[str] | None = None,
    headless: bool = False,
    uses_browser: bool | None = None,
    on_platform_done: Callable[[str, int], None] | None = None,
) -> Iterator[JobPosting]:
    """Yield postings from each platform.

    uses_browser=True/False limits the run to browser-based or API-based platforms
    (the microservices split them between the browser and discovery services).
    """
    for name in platforms or settings.enabled_platforms:
        cls = PLATFORMS.get(name)
        if not cls:
            log.warning("Unknown platform %r (available: %s)", name, ", ".join(PLATFORMS))
            continue
        if uses_browser is not None and cls.discovery_uses_browser != uses_browser:
            continue
        adapter = cls(settings, headless=headless)
        count = 0
        try:
            session = BrowserSession(adapter.profile, headless=headless) if adapter.discovery_uses_browser else nullcontext()
            with session as s:
                page = s.new_page() if s else None
                for posting in adapter.discover(page):
                    count += 1
                    yield posting
        except Exception as exc:
            log.error("Discovery failed on %s: %s", name, exc)
        log.info("%s: %d postings", name, count)
        if on_platform_done:
            on_platform_done(name, count)


def run_discovery(settings: Settings, platforms: list[str] | None = None, headless: bool = False) -> dict[str, int]:
    """CLI mode: write straight to the local database."""
    conn = db.connect()
    added = merge_registry(settings, [dict(r) for r in db.companies(conn, found_only=True)])
    if added:
        log.info("Including %d startup boards from the startup finder", added)
    stats: dict[str, int] = {}
    current: dict[str, int] = {}
    for posting in discover_postings(settings, platforms, headless):
        _, created = db.upsert_job(conn, posting)
        current[posting.platform] = current.get(posting.platform, 0) + int(created)
    stats.update(current)
    stats.update({f"match_{k}": v for k, v in match_pending(conn, settings).items()})
    conn.close()
    return stats
