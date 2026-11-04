"""SAP SuccessFactors career sites (SAP Labs, Wipro, HCLTech, EY and many other MNCs).

These sites share the same search page, so discovery reads it over plain HTTP:
  <site>/search/?q=<role>&locationsearch=<city>&startrow=<n>

Applying needs a candidate account on each company's site. Create them once with
`python -m jobagent login successfactors`. Application forms differ per company, so
expect more jobs in "Needs attention" here than on LinkedIn or Greenhouse.

Add sites in [platforms.successfactors] using the base URL, e.g. https://jobs.sap.com
"""

from __future__ import annotations

import html
import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator
from urllib.parse import urlencode, urljoin, urlparse

from ..answers import Answerer
from ..browser import body_contains, captcha_present, click_first, fill_form, wait_for_human
from ..http import get_text
from ..matching import clean_text, title_similarity
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

PAGE_SIZE = 25
ROW_RE = re.compile(r'<tr[^>]*class="[^"]*\bdata-row\b[^"]*"[^>]*>(.*?)</tr>', re.S | re.I)
LINK_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*jobTitle-link[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
LINK_RE_ALT = re.compile(r'<a[^>]*class="[^"]*jobTitle-link[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
LOC_RE = re.compile(r'<span[^>]*class="[^"]*jobLocation[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
DATE_RE = re.compile(r'<span[^>]*class="[^"]*jobDate[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
JOB_ID_RE = re.compile(r"/(\d{5,})/?(?:\?|$)")
DESC_MARKERS = ('class="jobdescription"', "itemprop=\"description\"", 'class="job-description"', 'id="jobdescription"')

SEL = {
    "apply": ["a.dialogApplyBtn", "button.dialogApplyBtn", "a:has-text('Apply now')", "button:has-text('Apply now')", "a:has-text('Apply')"],
    "apply_manually": ["a:has-text('Apply manually')", "button:has-text('Apply manually')", "button:has-text('Continue')"],
    "sign_in": "input[type='password']",
    "submit": ["button:has-text('Apply')", "button:has-text('Submit')", "input[type='submit'][value*='Apply' i]", "#fbclc_apply"],
    "next": ["button:has-text('Next')", "button:has-text('Save and Continue')", "button:has-text('Continue')"],
    "errors": ".errorMessage, [class*='error' i]:visible, [role='alert']",
}
SUCCESS_TEXT = [
    "thank you for applying",
    "successfully applied",
    "application has been submitted",
    "application was submitted",
    "we have received your application",
    "thank you for your application",
]
ALREADY_TEXT = ["already applied", "you have applied for this job"]


@dataclass
class SFSite:
    base: str
    company: str


def parse_site(entry: Any) -> SFSite:
    url = entry.get("url", "") if isinstance(entry, dict) else str(entry)
    parsed = urlparse(url.strip())
    if not parsed.netloc:
        raise ValueError(f"Invalid SuccessFactors URL: {url!r}")
    base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    company = entry.get("company") if isinstance(entry, dict) else None
    if not company:
        parts = parsed.netloc.split(".")
        company = (parts[-2] if len(parts) >= 2 else parts[0]).title()
    return SFSite(base=base, company=company)


def parse_search_page(page_html: str, base: str) -> list[dict[str, str]]:
    """Extract jobs from a SuccessFactors search results page."""
    jobs = []
    for row in ROW_RE.findall(page_html):
        m = LINK_RE.search(row) or LINK_RE_ALT.search(row)
        if not m:
            continue
        href, title_html = m.group(1), m.group(2)
        url = urljoin(base + "/", html.unescape(href))
        id_match = JOB_ID_RE.search(urlparse(url).path)
        loc = LOC_RE.search(row)
        date = DATE_RE.search(row)
        jobs.append({
            "id": id_match.group(1) if id_match else urlparse(url).path,
            "url": url,
            "title": clean_text(title_html),
            "location": clean_text(loc.group(1)) if loc else "",
            "posted": clean_text(date.group(1)) if date else "",
        })
    return jobs


def extract_description(page_html: str) -> str:
    lower = page_html.lower()
    for marker in DESC_MARKERS:
        i = lower.find(marker.lower())
        if i != -1:
            i = max(lower.rfind("<", 0, i), 0)  # start at the opening tag so it gets stripped
            return clean_text(re.sub(r"(?is)<(script|style).*?</\1>", " ", page_html[i : i + 30000]))[:15000]
    return ""


class SuccessFactors(Platform):
    name = "successfactors"
    profile = "successfactors"
    discovery_uses_browser = False

    def sites(self) -> list[SFSite]:
        out = []
        for entry in self.config.get("sites", []):
            try:
                out.append(parse_site(entry))
            except ValueError as exc:
                log.warning("%s", exc)
        return out

    def login_urls(self) -> list[str]:
        return [s.base for s in self.sites()]

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        prefs = self.settings.preferences
        roles = prefs.get("roles", [])
        locations = [loc for loc in prefs.get("locations", []) if loc.lower() != "remote"] or [""]
        max_pages = int(self.config.get("max_pages", 2))
        max_details = int(self.config.get("max_details_per_site", 40))
        for site in self.sites():
            seen: set[str] = set()
            details = 0
            for role in roles:
                for loc in locations:
                    for page_no in range(max_pages):
                        params = {"q": role, "startrow": page_no * PAGE_SIZE}
                        if loc:
                            params["locationsearch"] = loc
                        url = f"{site.base}/search/?{urlencode(params)}"
                        try:
                            rows = parse_search_page(get_text(url), site.base)
                        except Exception as exc:
                            log.warning("SuccessFactors %s search failed: %s", site.base, exc)
                            break
                        if not rows:
                            if page_no == 0 and not seen:
                                log.info("SuccessFactors %s: no results for %r @ %r (or the page layout differs)", site.company, role, loc)
                            break
                        for r in rows:
                            if r["id"] in seen:
                                continue
                            seen.add(r["id"])
                            if max(title_similarity(r["title"], x) for x in roles) < 0.5:
                                continue
                            description = ""
                            if details < max_details:
                                details += 1
                                try:
                                    description = extract_description(get_text(r["url"]))
                                except Exception as exc:
                                    log.debug("detail %s failed: %s", r["url"], exc)
                            yield JobPosting(
                                platform=self.name,
                                external_id=f"{urlparse(site.base).netloc}:{r['id']}",
                                url=r["url"],
                                title=r["title"],
                                company=site.company,
                                location=r["location"],
                                description=description,
                                posted_at=r["posted"] or None,
                            )
                        if len(rows) < PAGE_SIZE:
                            break
            log.info("SuccessFactors %s: %d jobs seen", site.company, len(seen))

    def fetch_description(self, page: "Page", job: sqlite3.Row) -> str | None:
        try:
            return extract_description(get_text(job["url"])) or None
        except Exception:
            return None

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        page.goto(job["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if body_contains(page, ["no longer available", "job has expired", "position has been filled"]):
            return ApplyResult("failed", "Job is closed")
        if not click_first(page, SEL["apply"], timeout=5000):
            return ApplyResult("needs_attention", "Apply button not found")
        page.wait_for_timeout(2500)
        click_first(page, SEL["apply_manually"], timeout=2000)
        page.wait_for_timeout(2500)

        if page.locator(SEL["sign_in"]).count():
            if not wait_for_human(
                page,
                f"Sign in to {job['company']}'s careers site (or create an account).",
                lambda: not page.locator(SEL["sign_in"]).count(),
                self.headless,
                timeout_s=240,
            ):
                return ApplyResult("needs_attention", "Sign-in required. Run: python -m jobagent login successfactors")
            page.wait_for_timeout(3000)
        if body_contains(page, ALREADY_TEXT):
            return ApplyResult("applied", "Already applied earlier")

        resume = self.settings.resume_path
        for _ in range(6):
            if captcha_present(page) and not wait_for_human(
                page, "Security check", lambda: not captcha_present(page), self.headless
            ):
                return ApplyResult("needs_attention", "CAPTCHA required")
            unanswered = fill_form(page, page, answerer, resume)
            if unanswered:
                return ApplyResult("needs_attention", "Unanswered: " + "; ".join(unanswered[:8]))

            # Multi-page forms: move to the next page while there is one.
            if click_first(page, SEL["next"], timeout=1000):
                page.wait_for_timeout(3000)
                if body_contains(page, SUCCESS_TEXT):
                    return ApplyResult("applied")
                continue

            # Last page: submit.
            if dry_run:
                return ApplyResult("dry_run", "Form filled, not submitted")
            if not click_first(page, SEL["submit"], timeout=2000):
                return ApplyResult("needs_attention", "No Next/Apply button found")
            page.wait_for_timeout(5000)
            if body_contains(page, SUCCESS_TEXT):
                return ApplyResult("applied")
            errors = [e.strip() for e in page.locator(SEL["errors"]).all_inner_texts() if e.strip()]
            return ApplyResult("needs_attention", "Not confirmed. " + "; ".join(errors[:5]))
        return ApplyResult("needs_attention", "Too many steps")
