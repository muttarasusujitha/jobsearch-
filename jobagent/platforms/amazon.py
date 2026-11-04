"""Amazon (amazon.jobs). Amazon runs its own careers site with a public JSON search.

Discovery and matching are automatic. Applying is NOT automated: Amazon's application
needs an Amazon Jobs account and often an online assessment, so matched jobs are put
in "Needs attention" with the link for you to apply by hand. The email tracker still
follows them once you click "I applied" in the dashboard.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Iterator
from urllib.parse import urlencode

from ..answers import Answerer
from ..http import get_json
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

SEARCH = "https://www.amazon.jobs/en/search.json?"
PAGE_SIZE = 50


class Amazon(Platform):
    name = "amazon"
    profile = "amazon"
    discovery_uses_browser = False
    manual_apply = True

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        prefs = self.settings.preferences
        max_pages = int(self.config.get("max_pages", 2))
        seen: set[str] = set()
        for role in prefs.get("roles", []):
            for loc in prefs.get("locations", []) or [""]:
                for page_no in range(max_pages):
                    params = {
                        "base_query": role,
                        "result_limit": PAGE_SIZE,
                        "offset": page_no * PAGE_SIZE,
                        "sort": "recent",
                    }
                    if loc and loc.lower() != "remote":
                        params["loc_query"] = loc
                    try:
                        data = get_json(SEARCH + urlencode(params))
                    except Exception as exc:
                        log.warning("Amazon search %r @ %r failed: %s", role, loc, exc)
                        break
                    jobs = data.get("jobs") or []
                    for j in jobs:
                        job_id = str(j.get("id_icims") or j.get("id") or "")
                        if not job_id or job_id in seen:
                            continue
                        seen.add(job_id)
                        description = "\n".join(
                            str(j.get(k) or "")
                            for k in ("description", "basic_qualifications", "preferred_qualifications")
                        )
                        path = j.get("job_path") or f"/en/jobs/{job_id}"
                        yield JobPosting(
                            platform=self.name,
                            external_id=job_id,
                            url=f"https://www.amazon.jobs{path}",
                            title=j.get("title", ""),
                            company=j.get("company_name") or "Amazon",
                            location=j.get("normalized_location") or j.get("location", ""),
                            description=description,
                            posted_at=j.get("posted_date"),
                        )
                    if len(jobs) < PAGE_SIZE:
                        break

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        return ApplyResult("needs_attention", f"Apply by hand on amazon.jobs: {job['url']}")
