"""Lever job boards. Discovery uses Lever's public postings API (no login)."""

from __future__ import annotations

import logging
import sqlite3
import urllib.error
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterator

from ..answers import Answerer
from ..browser import body_contains, captcha_present, click_first, fill_form, wait_for_human
from ..http import get_json
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

API = "https://api.lever.co/v0/postings/{company}?mode=json"
SUCCESS_TEXT = ["application submitted", "thanks for applying", "thank you for applying", "we've received your application"]


class Lever(Platform):
    name = "lever"
    profile = "ats"
    discovery_uses_browser = False

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        for company in self.config.get("companies", []):
            try:
                postings = get_json(API.format(company=company))
            except urllib.error.HTTPError as exc:
                log.warning("Lever company %r not found (%s). Check the slug.", company, exc.code)
                continue
            except Exception as exc:
                log.warning("Lever company %r failed: %s", company, exc)
                continue
            for p in postings:
                cats = p.get("categories") or {}
                parts = [p.get("descriptionPlain", "")]
                for section in p.get("lists", []):
                    parts.append(section.get("text", ""))
                    parts.append(section.get("content", ""))
                parts.append(p.get("additionalPlain", ""))
                created = p.get("createdAt")
                posted = (
                    datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat(timespec="seconds")
                    if isinstance(created, (int, float))
                    else None
                )
                location = cats.get("location") or ", ".join(cats.get("allLocations", []) or [])
                if p.get("workplaceType") == "remote" and "remote" not in location.lower():
                    location = f"{location} (Remote)".strip()
                yield JobPosting(
                    platform=self.name,
                    external_id=f"{company}:{p['id']}",
                    url=p.get("hostedUrl", ""),
                    apply_url=p.get("applyUrl") or f"{p.get('hostedUrl', '')}/apply",
                    title=p.get("text", ""),
                    company=company.replace("-", " ").title(),
                    location=location,
                    description="\n".join(x for x in parts if x),
                    posted_at=posted,
                )

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        page.goto(job["apply_url"] or job["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        if body_contains(page, ["no longer accepting", "posting is closed", "not found"]):
            return ApplyResult("failed", "Job is closed")

        form = page.locator("form#application-form, form.application-form, form").first
        # Upload the resume first: Lever parses it and pre-fills name/email/phone.
        resume = self.settings.resume_path
        if resume and resume.exists():
            try:
                page.locator("input[type='file'][name='resume']").set_input_files(str(resume))
                page.wait_for_timeout(4000)
            except Exception:
                pass
        unanswered = fill_form(page, form, answerer, resume)
        if unanswered:
            return ApplyResult("needs_attention", "Unanswered: " + "; ".join(unanswered[:8]))
        if dry_run:
            return ApplyResult("dry_run", "Form filled, not submitted")

        if not click_first(page, ["#btn-submit", "button[type='submit']", "button:has-text('Submit application')"]):
            return ApplyResult("needs_attention", "Submit button not found")
        page.wait_for_timeout(4000)

        def done() -> bool:
            return page.url.rstrip("/").endswith("thanks") or body_contains(page, SUCCESS_TEXT)

        if done():
            return ApplyResult("applied")
        if captcha_present(page):
            if wait_for_human(page, "Lever is showing a CAPTCHA. Solve it and click Submit.", done, self.headless):
                return ApplyResult("applied", "Completed after CAPTCHA")
            return ApplyResult("needs_attention", "CAPTCHA required")
        errors = page.locator(".error-message:visible, [class*='error' i]:visible").all_inner_texts()[:5]
        return ApplyResult("needs_attention", "Not confirmed. " + "; ".join(e.strip() for e in errors if e.strip()))
