"""Wellfound (formerly AngelList Talent), for startup jobs.

Log in once with `python -m jobagent login wellfound` and set your role/location
preferences on Wellfound itself: the /jobs page lists jobs based on those preferences.
Applying sends your Wellfound profile plus a short note (your cover letter template).
Wellfound often shows a bot check; the agent pauses so you can complete it.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING, Iterator

from ..answers import Answerer
from ..browser import body_contains, captcha_present, click_first, fill_form, wait_for_human
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

SEL = {
    "company_block": "[data-test='StartupResult'], div[class*='styles_component'][class*='startup' i]",
    "company_name": "h2, a[href*='/company/'] h2, a[href*='/company/']",
    "job_link": "a[href*='/jobs/'][href*='-']",
    "description": "[class*='description' i], #job-description, main",
    "apply": ["button:has-text('Apply')", "a:has-text('Apply')"],
    "modal": "[role='dialog'], div[class*='modal' i]",
    "note": "textarea",
    "send": ["button:has-text('Send application')", "button:has-text('Submit application')", "button[type='submit']"],
}
SUCCESS_TEXT = ["application sent", "you've applied", "you applied", "application submitted"]


class Wellfound(Platform):
    name = "wellfound"
    profile = "wellfound"
    login_url = "https://wellfound.com/login"

    def is_logged_in(self, page: "Page") -> bool:
        page.goto("https://wellfound.com/jobs", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if captcha_present(page):
            wait_for_human(page, "Wellfound bot check", lambda: not captcha_present(page), self.headless)
        return "login" not in page.url

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        assert page is not None
        if not self.is_logged_in(page):
            raise RuntimeError("Not logged in to Wellfound. Run: python -m jobagent login wellfound")
        seen: set[str] = set()
        for _ in range(int(self.config.get("max_pages", 1))):
            for _ in range(6):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(1200)
            blocks = page.locator(SEL["company_block"])
            for i in range(blocks.count()):
                block = blocks.nth(i)
                company = ""
                try:
                    company = block.locator(SEL["company_name"]).first.inner_text(timeout=800).split("\n")[0].strip()
                except Exception:
                    pass
                links = block.locator(SEL["job_link"])
                for j in range(links.count()):
                    link = links.nth(j)
                    href = link.get_attribute("href") or ""
                    m = re.search(r"/jobs/(\d+)", href)
                    if not m or m.group(1) in seen:
                        continue
                    seen.add(m.group(1))
                    lines = [x.strip() for x in link.inner_text().split("\n") if x.strip()]
                    title = lines[0] if lines else ""
                    location = " ".join(lines[1:3])
                    url = href if href.startswith("http") else f"https://wellfound.com{href}"
                    yield JobPosting(self.name, m.group(1), url, title, company, location)
            if not click_first(page, ["button:has-text('Load more')", "button:has-text('Show more')"], 1500):
                break

    def fetch_description(self, page: "Page", job: sqlite3.Row) -> str | None:
        page.goto(job["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if captcha_present(page):
            wait_for_human(page, "Wellfound bot check", lambda: not captcha_present(page), self.headless)
        try:
            return page.locator(SEL["description"]).first.inner_text(timeout=5000)
        except Exception:
            return None

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        if job["url"] not in page.url:
            page.goto(job["url"], wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        if captcha_present(page) and not wait_for_human(
            page, "Wellfound bot check", lambda: not captcha_present(page), self.headless
        ):
            return ApplyResult("needs_attention", "Bot check required")
        if page.locator("button:has-text('Applied')").count():
            return ApplyResult("applied", "Already applied earlier")
        if not click_first(page, SEL["apply"], timeout=4000):
            return ApplyResult("needs_attention", "Apply button not found")
        page.wait_for_timeout(2000)

        modal = page.locator(SEL["modal"]).first
        scope = modal if modal.count() else page.locator("body")
        note = scope.locator(SEL["note"]).first
        if note.count() and not note.input_value():
            note.fill(answerer.cover_letter)
        unanswered = fill_form(page, scope, answerer, self.settings.resume_path)
        if unanswered:
            return ApplyResult("needs_attention", "Unanswered: " + "; ".join(unanswered[:8]))
        if dry_run:
            return ApplyResult("dry_run", "Application filled, not sent")
        if not click_first(scope, SEL["send"]):
            return ApplyResult("needs_attention", "Send button not found")
        page.wait_for_timeout(3000)
        if body_contains(page, SUCCESS_TEXT):
            return ApplyResult("applied")
        return ApplyResult("needs_attention", "No confirmation after sending")
