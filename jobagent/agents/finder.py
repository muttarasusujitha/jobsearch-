"""CLI mode for the startup finder: check startups and save results in the local database."""

from __future__ import annotations

import logging

from .. import db
from ..config import Settings
from ..startups import find_startups, load_seeds

log = logging.getLogger(__name__)


def run_finder(settings: Settings, cities: list[str] | None = None) -> dict[str, int]:
    cities = [c.lower() for c in (cities or settings.section("startups").get("cities", ["bangalore", "hyderabad"]))]
    conn = db.connect()
    names = [s for s in load_seeds() if not cities or s.get("city", "").lower() in cities]
    names += db.company_candidates(conn, cities)
    stats = {"checked": 0, "found": 0}
    for record in find_startups(names, skip_keys=db.recently_checked(conn)):
        db.upsert_company(conn, record)
        stats["checked"] += 1
        stats["found"] += int(bool(record["ats"]))
    conn.close()
    log.info("Startup finder: %d checked, %d with a public careers board", stats["checked"], stats["found"])
    return stats
