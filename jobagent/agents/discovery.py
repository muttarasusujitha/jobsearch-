"""Discovery agent: collects job openings from every enabled platform."""

from __future__ import annotations

import logging
from contextlib import nullcontext

from .. import db
from ..browser import BrowserSession
from ..config import Settings
from ..platforms import PLATFORMS
from .matcher import match_pending

log = logging.getLogger(__name__)


def run_discovery(settings: Settings, platforms: list[str] | None = None, headless: bool = False) -> dict[str, int]:
    conn = db.connect()
    names = platforms or settings.enabled_platforms
    stats: dict[str, int] = {}
    for name in names:
        cls = PLATFORMS.get(name)
        if not cls:
            log.warning("Unknown platform %r (available: %s)", name, ", ".join(PLATFORMS))
            continue
        adapter = cls(settings, headless=headless)
        new = 0
        try:
            session = BrowserSession(adapter.profile, headless=headless) if adapter.discovery_uses_browser else nullcontext()
            with session as s:
                page = s.new_page() if s else None
                for posting in adapter.discover(page):
                    _, created = db.upsert_job(conn, posting)
                    new += int(created)
        except Exception as exc:
            log.error("Discovery failed on %s: %s", name, exc)
        stats[name] = new
        log.info("%s: %d new jobs", name, new)
    stats.update({f"match_{k}": v for k, v in match_pending(conn, settings).items()})
    conn.close()
    return stats
