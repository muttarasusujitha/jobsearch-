"""Workday career sites (*.myworkdayjobs.com), used by Accenture and many other MNCs.

Discovery calls the JSON endpoint that every Workday career site loads its job list
from, so it doesn't need a browser. Applying does: each company's Workday site needs
its own candidate account. Create them once with `python -m jobagent login workday`.

Add sites in [platforms.workday] using the URL of the company's job list, e.g.
  https://accenture.wd103.myworkdayjobs.com/AccentureCareers

Workday pages use stable `data-automation-id` attributes; they're listed in SEL.
"""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import urllib.error
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator
from urllib.parse import urlparse

from ..answers import Answerer
from ..browser import body_contains, captcha_present, click_first, fill_form, page_text, wait_for_human
from ..http import get_json, post_json
from ..matching import title_similarity
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

PAGE_SIZE = 20
LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")

SEL = {
    "apply": ["a[data-automation-id='adventureButton']", "button[data-automation-id='adventureButton']", "a:has-text('Apply')"],
    "use_last": ["a[data-automation-id='useMyLastApplication']", "button[data-automation-id='useMyLastApplication']"],
    "apply_manually": ["a[data-automation-id='applyManually']", "button[data-automation-id='applyManually']"],
    "sign_in_form": "[data-automation-id='signInContent'], [data-automation-id='signInSubmitButton'], [data-automation-id='createAccountLink']",
    "flow_page": "[data-automation-id='applyFlowPage'], [data-automation-id='applyFlowContainer'], main",
    "next": [
        "button[data-automation-id='bottom-navigation-next-button']",
        "button[data-automation-id='pageFooterNextButton']",
        "button:has-text('Save and Continue')",
        "button:has-text('Next')",
    ],
    "file_input": "input[data-automation-id='file-upload-input-ref'], input[type='file']",
    "uploaded_file": "[data-automation-id='file-upload-successful'], [data-automation-id='fileName'], [data-automation-id='file-upload-item']",
    "errors": "[data-automation-id='errorMessage'], [data-automation-id='inputAlert'], [role='alert']",
    "page_title": "[data-automation-id='progressBarActiveStep'], h2",
}
SUCCESS_TEXT = ["application submitted", "successfully submitted", "congratulations", "thank you for applying", "thanks for applying"]
ALREADY_TEXT = ["you've already applied", "you have already applied", "already applied for this job"]


@dataclass
class WorkdaySite:
    url: str      # human URL of the job list, without locale
    api: str      # .../wday/cxs/<tenant>/<site>
    company: str


def parse_site(entry: Any) -> WorkdaySite:
    """Accepts "https://tenant.wd5.myworkdayjobs.com/en-US/Site" or {url=..., company=...}."""
    url = entry.get("url", "") if isinstance(entry, dict) else str(entry)
    parsed = urlparse(url.strip())
    host = parsed.netloc
    if not host:
        raise ValueError(f"Invalid Workday URL: {url!r}")
    parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("myworkdaysite.com") and parts[:1] == ["recruiting"]:
        parts = parts[1:]  # wdN.myworkdaysite.com/recruiting/<tenant>/<site>
        if len(parts) < 2:
            raise ValueError(f"Workday URL needs /recruiting/<tenant>/<site>: {url!r}")
        tenant, site = parts[0], parts[1]
        base = f"https://{host}/recruiting/{tenant}/{site}"
    else:
        parts = [p for p in parts if not LOCALE_RE.match(p)]
        if not parts:
            raise ValueError(f"Workday URL needs the site name, e.g. .../AccentureCareers: {url!r}")
        tenant, site = host.split(".")[0], parts[0]
        base = f"https://{host}/{site}"
    company = entry.get("company") if isinstance(entry, dict) else None
    return WorkdaySite(url=base, api=f"https://{host}/wday/cxs/{tenant}/{site}", company=company or tenant.title())


class Workday(Platform):
    name = "workday"
    profile = "workday"
    discovery_uses_browser = False

    def sites(self) -> list[WorkdaySite]:
        out = []
        for entry in self.config.get("sites", []):
            try:
                out.append(parse_site(entry))
            except ValueError as exc:
                log.warning("%s", exc)
        return out

    def login_urls(self) -> list[str]:
        return [s.url for s in self.sites()]

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        roles = self.settings.preferences.get("roles", [])
        max_pages = int(self.config.get("max_pages", 3))
        max_details = int(self.config.get("max_details_per_site", 60))
        for site in self.sites():
            seen: set[str] = set()
            details = 0
            for role in roles:
                total = None
                for page_no in range(max_pages):
                    payload = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": page_no * PAGE_SIZE, "searchText": role}
                    try:
                        data = post_json(f"{site.api}/jobs", payload)
                    except urllib.error.HTTPError as exc:
                        log.warning("Workday %s: HTTP %s. Check the site URL.", site.url, exc.code)
                        break
                    except Exception as exc:
                        log.warning("Workday %s failed: %s", site.url, exc)
                        break
                    if total is None:
                        total = int(data.get("total") or 0)  # only reliable on the first page
                    postings = data.get("jobPostings") or []
                    new_on_page = 0
                    for p in postings:
                        path = p.get("externalPath")
                        if not path or path in seen:
                            continue
                        seen.add(path)
                        new_on_page += 1
                        title = p.get("title", "")
                        # Skip the detail request for titles that can't match.
                        if max(title_similarity(title, r) for r in roles) < 0.5:
                            continue
                        description = ""
                        posted = p.get("postedOn")
                        if details < max_details:
                            details += 1
                            info = self._details(site, path)
                            description = html.unescape(info.get("jobDescription") or "")
                            posted = info.get("startDate") or posted
                        yield JobPosting(
                            platform=self.name,
                            external_id=f"{site.api.rsplit('/cxs/', 1)[1]}:{path}",
                            url=f"{site.url}{path}",
                            title=title,
                            company=site.company,
                            location=p.get("locationsText", ""),
                            description=description,
                            posted_at=posted,
                        )
                    # Workday's offset loops back to the start past the end, so stop on repeats.
                    if not postings or new_on_page == 0 or (page_no + 1) * PAGE_SIZE >= (total or 0):
                        break
            log.info("Workday %s: %d jobs seen, %d detail lookups", site.company, len(seen), details)

    def _details(self, site: WorkdaySite, path: str) -> dict[str, Any]:
        try:
            return get_json(f"{site.api}{path}").get("jobPostingInfo") or {}
        except Exception as exc:
            log.debug("Workday detail %s failed: %s", path, exc)
            return {}

    def fetch_description(self, page: "Page", job: sqlite3.Row) -> str | None:
        for site in self.sites():
            if job["url"].startswith(site.url):
                info = self._details(site, job["url"][len(site.url):])
                return html.unescape(info.get("jobDescription") or "") or None
        return None

    # ------------------------------------------------------------------ apply
    def _signed_in_flow_visible(self, page: "Page") -> bool:
        if page.locator(SEL["sign_in_form"]).count():
            return False
        return any(page.locator(s).count() for s in SEL["next"])

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        page.goto(job["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        if body_contains(page, ALREADY_TEXT):
            return ApplyResult("applied", "Already applied earlier")
        if body_contains(page, ["no longer accepting", "job posting is no longer available", "page you are looking for doesn't exist"]):
            return ApplyResult("failed", "Job is closed")
        if not click_first(page, SEL["apply"], timeout=5000):
            return ApplyResult("needs_attention", "Apply button not found")
        page.wait_for_timeout(2000)
        # Prefer re-using your last Workday application for this company; it pre-fills almost everything.
        if not click_first(page, SEL["use_last"], timeout=2000):
            click_first(page, SEL["apply_manually"], timeout=3000)
        page.wait_for_timeout(3000)

        if page.locator(SEL["sign_in_form"]).count():
            if not wait_for_human(
                page,
                f"Sign in to {job['company']}'s Workday site (or create an account).",
                lambda: self._signed_in_flow_visible(page),
                self.headless,
                timeout_s=240,
            ):
                return ApplyResult("needs_attention", "Workday sign-in required. Run: python -m jobagent login workday")

        resume = self.settings.resume_path
        last_step = ""
        for _ in range(12):
            page.wait_for_timeout(2500)
            if body_contains(page, SUCCESS_TEXT):
                return ApplyResult("applied")
            if captcha_present(page) and not wait_for_human(
                page, "Workday security check", lambda: not captcha_present(page), self.headless
            ):
                return ApplyResult("needs_attention", "CAPTCHA required")

            step = _first_text(page, SEL["page_title"])
            scope = page.locator(SEL["flow_page"]).first
            self._upload_resume(page, resume)
            unanswered = fill_form(page, scope, answerer, resume)
            if unanswered:
                # Workday keeps your progress, so you can finish this by hand later.
                return ApplyResult("needs_attention", f"[{step}] Unanswered: " + "; ".join(unanswered[:8]))

            next_text = ""
            for sel in SEL["next"]:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible():
                    next_text = btn.inner_text().strip().lower()
                    break
            if not next_text:
                return ApplyResult("needs_attention", f"[{step}] No Next/Submit button")
            if next_text.startswith("submit"):
                if dry_run:
                    return ApplyResult("dry_run", "Reached the Review page, not submitted")
                click_first(page, SEL["next"])
                page.wait_for_timeout(5000)
                if body_contains(page, SUCCESS_TEXT):
                    return ApplyResult("applied")
                return ApplyResult("needs_attention", "Submitted but no confirmation seen")

            click_first(page, SEL["next"])
            page.wait_for_timeout(2500)
            errors = [e.strip() for e in page.locator(SEL["errors"]).all_inner_texts() if e.strip()]
            new_step = _first_text(page, SEL["page_title"])
            if errors and new_step == step:
                return ApplyResult("needs_attention", f"[{step}] " + "; ".join(errors[:5]))
            if new_step == step == last_step:
                return ApplyResult("needs_attention", f"Stuck on step '{step}'. " + page_text(scope, 200))
            last_step = step
        return ApplyResult("needs_attention", "Too many steps")

    def _upload_resume(self, page: "Page", resume: Any) -> None:
        if not resume or not resume.exists():
            return
        try:
            file_input = page.locator(SEL["file_input"]).first
            if file_input.count() and not page.locator(SEL["uploaded_file"]).count():
                file_input.set_input_files(str(resume))
                page.wait_for_timeout(5000)
        except Exception as exc:
            log.debug("resume upload skipped: %s", exc)


def _first_text(page: "Page", selector: str) -> str:
    try:
        loc = page.locator(selector).first
        return loc.inner_text(timeout=1500).strip() if loc.count() else ""
    except Exception:
        return ""
