from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterator

from ..answers import Answerer
from ..config import Settings
from ..models import ApplyResult, JobPosting

if TYPE_CHECKING:
    from playwright.sync_api import Page


class Platform(ABC):
    """One job site. Subclasses implement discovery and applying."""

    name: str = ""
    # Browser profile folder. Sites sharing a login can share a profile.
    profile: str = ""
    # True if discovery needs a logged-in browser (LinkedIn, Naukri...).
    discovery_uses_browser: bool = True
    login_url: str | None = None
    # Text that appears on the page only when logged in (used by `login`).
    logged_in_check: str | None = None
    # True if the agent only finds and matches jobs; you apply by hand from the dashboard.
    manual_apply: bool = False

    def __init__(self, settings: Settings, headless: bool = False):
        self.settings = settings
        self.config = settings.platform(self.name)
        self.headless = headless

    @abstractmethod
    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        """Yield job postings. `page` is None when discovery_uses_browser is False."""

    def fetch_description(self, page: "Page", job: sqlite3.Row) -> str | None:
        """Return the full description if discovery only had a summary."""
        return None

    @abstractmethod
    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        """Submit an application. With dry_run=True, fill everything but don't submit."""

    def is_logged_in(self, page: "Page") -> bool:
        return True

    def login_urls(self) -> list[str]:
        """Pages to open for `python -m jobagent login <platform>`."""
        return [self.login_url] if self.login_url else []
