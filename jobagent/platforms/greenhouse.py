"""Greenhouse job boards. Discovery uses the public Job Board API, so no login is needed.

Many MNCs and product companies (Razorpay, Postman, Groww, Stripe, Airbnb...) host
their careers page on Greenhouse. Add their board token in [platforms.greenhouse].
"""

from __future__ import annotations

import logging
import sqlite3
import urllib.error
from typing import TYPE_CHECKING, Iterator

from ..answers import Answerer
from ..browser import body_contains, captcha_present, click_first, fill_form, wait_for_human
from ..http import get_json
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards/{board}"
FORM_URL = "https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
SUCCESS_TEXT = [
    "thank you for applying",
    "application has been received",
    "application was submitted",
    "we've received your application",
    "your application has been submitted",
]
SUBMIT_SELECTORS = [
    "button[type='submit']:has-text('Submit')",
    "#submit_app",
    "input[type='submit']",
    "button:has-text('Submit application')",
]


class Greenhouse(Platform):
    name = "greenhouse"
    profile = "ats"
    discovery_uses_browser = False

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        for board in self.config.get("boards", []):
            try:
                meta = get_json(API.format(board=board))
                company = meta.get("name") or board
            except Exception:
                company = board
            try:
                data = get_json(API.format(board=board) + "/jobs?content=true")
            except urllib.error.HTTPError as exc:
                log.warning("Greenhouse board %r not found (%s). Check the token.", board, exc.code)
                continue
            except Exception as exc:
                log.warning("Greenhouse board %r failed: %s", board, exc)
                continue
            for j in data.get("jobs", []):
                job_id = str(j["id"])
                yield JobPosting(
                    platform=self.name,
                    external_id=f"{board}:{job_id}",
                    url=j.get("absolute_url") or FORM_URL.format(board=board, job_id=job_id),
                    apply_url=FORM_URL.format(board=board, job_id=job_id),
                    title=j.get("title", ""),
                    company=company,
                    location=(j.get("location") or {}).get("name", ""),
                    description=j.get("content", ""),
                    posted_at=j.get("first_published") or j.get("updated_at"),
                )

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        page.goto(job["apply_url"] or job["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        if body_contains(page, ["no longer open", "job not found", "position has been filled"]):
            return ApplyResult("failed", "Job is closed")

        click_first(page, ["button:has-text('Apply')", "a:has-text('Apply for this job')"], timeout=1500)
        page.wait_for_timeout(1000)
        form = page.locator("form#application-form, form#application_form, form").first
        unanswered = fill_form(page, form, answerer, self.settings.resume_path)
        if unanswered:
            return ApplyResult("needs_attention", "Unanswered: " + "; ".join(unanswered[:8]))
        if dry_run:
            return ApplyResult("dry_run", "Form filled, not submitted")

        if not click_first(page, SUBMIT_SELECTORS):
            return ApplyResult("needs_attention", "Submit button not found")
        page.wait_for_timeout(4000)

        def done() -> bool:
            return "confirmation" in page.url or body_contains(page, SUCCESS_TEXT)

        if done():
            return ApplyResult("applied")
        if captcha_present(page) or body_contains(page, ["security code", "verification code"]):
            if wait_for_human(page, "Greenhouse needs a CAPTCHA or email security code, then click Submit.", done, self.headless):
                return ApplyResult("applied", "Completed after manual verification")
            return ApplyResult("needs_attention", "CAPTCHA or email verification required")
        errors = page.locator("[class*='error' i]:visible, [aria-invalid='true']").all_inner_texts()[:5]
        return ApplyResult("needs_attention", "Not confirmed. " + "; ".join(e.strip() for e in errors if e.strip()))
