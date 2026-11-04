"""LinkedIn Easy Apply.

Log in once with `python -m jobagent login linkedin`. LinkedIn's terms forbid automated
applying and it can restrict accounts that do it, so keep daily limits low.

LinkedIn changes its HTML often. All selectors are at the top of this file so they're
easy to update when something stops working.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING, Iterator
from urllib.parse import urlencode

from ..answers import Answerer
from ..browser import (
    body_contains,
    captcha_present,
    click_first,
    fill_form,
    page_text,
    scroll_element,
    wait_for_human,
)
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs/search/?"
VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}/"

SEL = {
    "card": "li[data-occludable-job-id], div[data-job-id]",
    "card_title": "a.job-card-list__title--link, a.job-card-container__link, a[href*='/jobs/view/']",
    "card_company": ".artdeco-entity-lockup__subtitle, .job-card-container__primary-description",
    "card_location": ".artdeco-entity-lockup__caption, .job-card-container__metadata-item",
    "list_scroller": ".jobs-search-results-list, .scaffold-layout__list > div, .scaffold-layout__list",
    "description": "#job-details, .jobs-description__content, .jobs-box__html-content, .jobs-description-content__text",
    "easy_apply": [
        "button.jobs-apply-button:has-text('Easy Apply')",
        "button:has-text('Easy Apply')",
        "a:has-text('Easy Apply')",
    ],
    "modal": "div.jobs-easy-apply-modal, div[data-test-modal][role='dialog'], div[role='dialog']",
    "submit": ["button[aria-label='Submit application']", "button:has-text('Submit application')"],
    "review": ["button[aria-label='Review your application']", "button:has-text('Review')"],
    "next": ["button[aria-label='Continue to next step']", "button:has-text('Next')", "button:has-text('Continue')"],
    "follow": "label[for='follow-company-checkbox'], input#follow-company-checkbox",
    "errors": ".artdeco-inline-feedback--error, [data-test-form-element-error-messages]",
    "dismiss": ["button[aria-label='Dismiss']", "button[aria-label='Close']"],
    "discard": ["button[data-control-name='discard_application_confirm_btn']", "button:has-text('Discard')"],
}
SENT_TEXT = ["application was sent", "application sent", "your application was sent"]


class LinkedIn(Platform):
    name = "linkedin"
    profile = "linkedin"
    login_url = "https://www.linkedin.com/login"

    def is_logged_in(self, page: "Page") -> bool:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        return "/feed" in page.url and "login" not in page.url

    def _search_urls(self) -> Iterator[tuple[str, str]]:
        prefs = self.settings.preferences
        for role in prefs.get("roles", []):
            for loc in prefs.get("locations", []) or [""]:
                params = {
                    "keywords": role,
                    "f_TPR": self.config.get("posted_within", "r604800"),
                    "sortBy": "DD",
                }
                if self.config.get("easy_apply_only", True):
                    params["f_AL"] = "true"
                if loc.lower() == "remote":
                    params["f_WT"] = "2"
                    params["location"] = self.config.get("remote_country", "India")
                elif loc:
                    params["location"] = loc
                yield f"{role} @ {loc}", SEARCH_URL + urlencode(params)

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        assert page is not None
        if not self.is_logged_in(page):
            raise RuntimeError("Not logged in to LinkedIn. Run: python -m jobagent login linkedin")
        seen: set[str] = set()
        for label, url in self._search_urls():
            for page_no in range(int(self.config.get("max_pages", 2))):
                page.goto(f"{url}&start={25 * page_no}", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                if captcha_present(page):
                    wait_for_human(page, "LinkedIn security check", lambda: not captcha_present(page), self.headless)
                scroll_element(page, SEL["list_scroller"], steps=10)
                cards = page.locator(SEL["card"])
                count = cards.count()
                log.info("LinkedIn %s page %d: %d cards", label, page_no + 1, count)
                if count == 0:
                    break
                for i in range(count):
                    job = self._parse_card(cards.nth(i))
                    if job and job.external_id not in seen:
                        seen.add(job.external_id)
                        yield job
                page.wait_for_timeout(1500)

    def _parse_card(self, card: "Locator") -> JobPosting | None:
        try:
            job_id = card.get_attribute("data-occludable-job-id") or card.get_attribute("data-job-id")
            link = card.locator(SEL["card_title"]).first
            title = (link.get_attribute("aria-label") or link.inner_text(timeout=1000)).strip()
            title = title.split("\n")[0].replace(" with verification", "").strip()
            if not job_id:
                href = link.get_attribute("href") or ""
                m = re.search(r"/jobs/view/(\d+)", href)
                job_id = m.group(1) if m else None
            if not job_id or not title:
                return None
            company = _first_text(card, SEL["card_company"])
            location = _first_text(card, SEL["card_location"])
            return JobPosting(
                platform=self.name,
                external_id=job_id,
                url=VIEW_URL.format(job_id=job_id),
                title=title,
                company=company,
                location=location,
            )
        except Exception as exc:
            log.debug("card parse failed: %s", exc)
            return None

    def fetch_description(self, page: "Page", job: sqlite3.Row) -> str | None:
        page.goto(job["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        click_first(page, ["button[aria-label*='see more' i]", "button:has-text('See more')"], timeout=1000)
        try:
            return page.locator(SEL["description"]).first.inner_text(timeout=5000)
        except Exception:
            return None

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        if job["url"] not in page.url:
            page.goto(job["url"], wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

        if body_contains(page, ["no longer accepting applications"]):
            return ApplyResult("failed", "No longer accepting applications")
        top = page_text(page, 3000).lower()
        if re.search(r"applied \d+ (minute|hour|day|week|month)s? ago", top):
            return ApplyResult("applied", "Already applied earlier")
        if not click_first(page, SEL["easy_apply"], timeout=4000):
            return ApplyResult("needs_attention", "No Easy Apply button (external application)")

        page.wait_for_timeout(1500)
        modal = page.locator(SEL["modal"]).first
        try:
            modal.wait_for(state="visible", timeout=8000)
        except Exception:
            return ApplyResult("needs_attention", "Easy Apply dialog did not open")

        last_text = ""
        for step in range(15):
            unanswered = fill_form(page, modal, answerer, self.settings.resume_path)
            if unanswered:
                self._discard(page)
                return ApplyResult("needs_attention", "Unanswered: " + "; ".join(unanswered[:8]))

            if modal.locator(SEL["submit"][1]).count() and modal.locator(SEL["submit"][1]).first.is_visible():
                if dry_run:
                    self._discard(page)
                    return ApplyResult("dry_run", f"Reached submit after {step + 1} steps")
                # Don't auto-follow every company you apply to.
                try:
                    box = page.locator("input#follow-company-checkbox")
                    if box.count() and box.is_checked():
                        page.locator("label[for='follow-company-checkbox']").click()
                except Exception:
                    pass
                click_first(modal, SEL["submit"])
                page.wait_for_timeout(3500)
                if body_contains(page, SENT_TEXT) or not page.locator(SEL["modal"]).count():
                    click_first(page, SEL["dismiss"], timeout=1500)
                    return ApplyResult("applied")
                return ApplyResult("needs_attention", "Submitted but no confirmation seen")

            if not (click_first(modal, SEL["review"], 1000) or click_first(modal, SEL["next"], 1000)):
                self._discard(page)
                return ApplyResult("needs_attention", "No Next/Review/Submit button in dialog")
            page.wait_for_timeout(1500)

            errors = [e.strip() for e in modal.locator(SEL["errors"]).all_inner_texts() if e.strip()]
            text = page_text(modal, 4000)
            if errors or text == last_text:
                self._discard(page)
                return ApplyResult("needs_attention", "Form error: " + "; ".join(errors[:5] or ["stuck on same step"]))
            last_text = text

        self._discard(page)
        return ApplyResult("needs_attention", "Too many steps")

    def _discard(self, page: "Page") -> None:
        if click_first(page, SEL["dismiss"], timeout=1500):
            page.wait_for_timeout(800)
            click_first(page, SEL["discard"], timeout=2000)


def _first_text(scope: "Locator", selector: str) -> str:
    try:
        loc = scope.locator(selector).first
        if loc.count():
            return loc.inner_text(timeout=1000).split("\n")[0].strip()
    except Exception:
        pass
    return ""
