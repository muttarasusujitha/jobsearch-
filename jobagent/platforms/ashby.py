"""Ashby job boards (used by many startups). Discovery uses Ashby's public posting API.

Add the company's board name from URLs like https://jobs.ashbyhq.com/<name>
to [platforms.ashby] companies. No login is needed.
"""

from __future__ import annotations

import logging
import sqlite3
import urllib.error
from typing import TYPE_CHECKING, Iterator
from urllib.parse import quote

from ..answers import Answerer
from ..browser import body_contains, captcha_present, click_first, fill_form, wait_for_human
from ..http import get_json
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
SUCCESS_TEXT = [
    "thanks for applying",
    "thank you for applying",
    "application was successfully submitted",
    "application has been submitted",
    "we've received your application",
]


class Ashby(Platform):
    name = "ashby"
    profile = "ats"
    discovery_uses_browser = False

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        for slug in self.config.get("companies", []):
            try:
                data = get_json(API.format(slug=quote(slug)))
            except urllib.error.HTTPError as exc:
                log.warning("Ashby board %r not found (%s). Check the name in jobs.ashbyhq.com/<name>.", slug, exc.code)
                continue
            except Exception as exc:
                log.warning("Ashby board %r failed: %s", slug, exc)
                continue
            for j in data.get("jobs", []):
                if j.get("isListed") is False:
                    continue
                locations = [j.get("location") or ""]
                for extra in j.get("secondaryLocations") or []:
                    locations.append(extra.get("location", "") if isinstance(extra, dict) else str(extra))
                location = ", ".join(x for x in locations if x)
                if j.get("isRemote") and "remote" not in location.lower():
                    location = f"{location} (Remote)".strip()
                job_url = j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j['id']}"
                yield JobPosting(
                    platform=self.name,
                    external_id=f"{slug}:{j['id']}",
                    url=job_url,
                    apply_url=j.get("applyUrl") or f"{job_url}/application",
                    title=j.get("title", ""),
                    company=slug.replace("-", " ").title(),
                    location=location,
                    description=j.get("descriptionPlain") or j.get("descriptionHtml") or "",
                    posted_at=j.get("publishedAt"),
                )

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        page.goto(job["apply_url"] or job["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if body_contains(page, ["job not found", "no longer accepting", "this job is closed"]):
            return ApplyResult("failed", "Job is closed")
        click_first(page, ["a:has-text('Apply for this Job')", "button:has-text('Apply for this Job')"], timeout=1500)
        page.wait_for_timeout(1500)

        # Upload the resume first: Ashby can autofill fields from it.
        resume = self.settings.resume_path
        if resume and resume.exists():
            try:
                page.locator("input[type='file']").first.set_input_files(str(resume))
                page.wait_for_timeout(4000)
            except Exception:
                pass
        form = page.locator("form, [class*='application-form' i]").first
        unanswered = fill_form(page, form if form.count() else page, answerer, resume)
        if unanswered:
            return ApplyResult("needs_attention", "Unanswered: " + "; ".join(unanswered[:8]))
        if dry_run:
            return ApplyResult("dry_run", "Form filled, not submitted")

        if not click_first(page, ["button:has-text('Submit Application')", "button[type='submit']"]):
            return ApplyResult("needs_attention", "Submit button not found")
        page.wait_for_timeout(4000)

        def done() -> bool:
            return body_contains(page, SUCCESS_TEXT)

        if done():
            return ApplyResult("applied")
        if captcha_present(page):
            if wait_for_human(page, "Ashby is showing a CAPTCHA. Solve it and submit.", done, self.headless):
                return ApplyResult("applied", "Completed after CAPTCHA")
            return ApplyResult("needs_attention", "CAPTCHA required")
        errors = page.locator("[class*='error' i]:visible").all_inner_texts()[:5]
        return ApplyResult("needs_attention", "Not confirmed. " + "; ".join(e.strip() for e in errors if e.strip()))
