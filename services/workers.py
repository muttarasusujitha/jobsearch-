"""Logic of the worker services. Each class handles messages from the bus.

discovery  - API-based job sources (Greenhouse, Lever, Ashby, Workday, SuccessFactors, Amazon)
browser    - browser-based job sources (LinkedIn, Naukri, Wellfound) and all applying
matcher    - scores new jobs
tracker    - reads your inbox
startups   - finds Bangalore/Hyderabad startups with public careers boards
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Callable

from jobagent import events
from jobagent.agents.applier import run_applications
from jobagent.agents.discovery import discover_postings
from jobagent.agents.matcher import row_to_posting
from jobagent.agents.tracker import fetch_messages
from jobagent.apiclient import ApiClient, ApiError
from jobagent.bus import Bus
from jobagent.config import Settings
from jobagent.matching import score_job
from jobagent.startups import find_startups, load_seeds, merge_registry

log = logging.getLogger("workers")
SettingsFn = Callable[[], Settings]


class Discovery:
    """Publishes every posting it finds; the API service de-duplicates and stores them."""

    def __init__(self, bus: Bus, api: ApiClient, settings: SettingsFn, uses_browser: bool = False, headless: bool = True):
        self.bus, self.api, self.settings = bus, api, settings
        self.uses_browser, self.headless = uses_browser, headless

    def handle(self, stream: str, cmd: dict[str, Any]) -> None:
        settings = self.settings()
        if not self.uses_browser:
            try:
                added = merge_registry(settings, self.api.companies(found_only=True))
                if added:
                    log.info("Including %d startup boards from the startup finder", added)
            except ApiError as exc:
                log.warning("Could not load startup boards: %s", exc)
        count = 0
        for posting in discover_postings(settings, cmd.get("platforms"), self.headless, uses_browser=self.uses_browser):
            self.bus.publish(events.JOB_DISCOVERED, asdict(posting))
            count += 1
        log.info("Discovery (%s) published %d postings", "browser" if self.uses_browser else "api", count)


class Matcher:
    def __init__(self, bus: Bus, settings: SettingsFn):
        self.bus, self.settings = bus, settings

    def handle(self, stream: str, job: dict[str, Any]) -> None:
        if not job or "id" not in job:
            return
        result = score_job(row_to_posting(job), self.settings().preferences)
        self.bus.publish(events.JOB_SCORED, {"id": job["id"], "score": result.score, "reasons": result.reasons, "passed": result.passed})


class ServiceStore:
    """ApplyStore backed by the API (reads) and the bus (writes)."""

    def __init__(self, bus: Bus, api: ApiClient):
        self.bus, self.api = bus, api

    def approved_jobs(self) -> list[dict[str, Any]]:
        return self.api.get("/jobs", status="approved", description="true", limit=2000)

    def applied_count(self, since: str, platform: str | None = None) -> int:
        return self.api.applied_count(since, platform)

    def save_description(self, job_id: int, description: str) -> None:
        self.bus.publish(events.JOB_DESCRIPTION, {"id": job_id, "description": description})

    def set_result(self, job_id: int, status: str, note: str) -> None:
        self.bus.publish(events.JOB_APPLY_RESULT, {"id": job_id, "status": status, "note": note})


class Browser:
    """One process, one browser at a time: browser discovery and applying run one after another."""

    def __init__(self, bus: Bus, api: ApiClient, settings: SettingsFn, headless: bool = False, sleep: Any = None):
        self.discovery = Discovery(bus, api, settings, uses_browser=True, headless=headless)
        self.store = ServiceStore(bus, api)
        self.settings, self.headless = settings, headless
        self.sleep = sleep

    def handle(self, stream: str, cmd: dict[str, Any]) -> None:
        if stream == events.CMD_DISCOVER:
            self.discovery.handle(stream, cmd)
        elif stream == events.CMD_APPLY:
            kwargs: dict[str, Any] = {"store": self.store}
            if self.sleep is not None:
                kwargs["sleep"] = self.sleep
            stats = run_applications(
                self.settings(), cmd.get("platforms"), headless=self.headless,
                dry_run=bool(cmd.get("dry_run")), limit=cmd.get("limit"), **kwargs,
            )
            log.info("Apply run finished: %s", stats)


class Tracker:
    def __init__(self, bus: Bus, settings: SettingsFn, fetch: Callable[[Settings], Any] = fetch_messages):
        self.bus, self.settings, self.fetch = bus, settings, fetch

    def handle(self, stream: str, cmd: dict[str, Any]) -> None:
        n = 0
        for message in self.fetch(self.settings()):
            self.bus.publish(events.EMAIL_RECEIVED, message)
            n += 1
        log.info("Tracker published %d emails", n)


class StartupFinder:
    def __init__(self, bus: Bus, api: ApiClient, settings: SettingsFn, fetch: Callable[[str], Any] | None = None, pause: float = 0.3):
        self.bus, self.api, self.settings = bus, api, settings
        self.fetch, self.pause = fetch, pause

    def handle(self, stream: str, cmd: dict[str, Any]) -> None:
        default = self.settings().section("startups").get("cities", ["bangalore", "hyderabad"])
        cities = [c.lower() for c in (cmd.get("cities") or default)]
        names = [s for s in load_seeds() if s.get("city", "").lower() in cities]
        try:
            names += self.api.company_candidates(cities)
            skip = set(self.api.recently_checked())
        except ApiError as exc:
            log.warning("API unavailable, checking the starter list only: %s", exc)
            skip = set()
        kwargs: dict[str, Any] = {"pause": self.pause}
        if self.fetch:
            kwargs["fetch"] = self.fetch
        found = checked = 0
        for record in find_startups(names, skip_keys=skip, **kwargs):
            self.bus.publish(events.COMPANY_CHECKED, record)
            checked += 1
            found += int(bool(record["ats"]))
        log.info("Startup finder: %d checked, %d with a public careers board", checked, found)
