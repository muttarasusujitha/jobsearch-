"""Naukri.com.

Log in once with `python -m jobagent login naukri`. Naukri's "Apply" either applies
instantly, opens a chatbot with screening questions, or sends you to the company site.
The chatbot questions are answered with the same rules as other forms.
Selectors are at the top of the file for easy updates.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING, Iterator

from ..answers import Answerer, Question
from ..browser import body_contains, captcha_present, click_first, page_text, wait_for_human
from ..models import ApplyResult, JobPosting
from .base import Platform

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

log = logging.getLogger(__name__)

SEL = {
    "card": ".srp-jobtuple-wrapper, article.jobTuple, div.cust-job-tuple",
    "card_title": "a.title",
    "card_company": "a.comp-name, .comp-name",
    "card_location": ".locWdth, .loc-wrap, .location",
    "card_exp": ".expwdth, .exp-wrap, .experience",
    "description": ".styles_JDC__dang-inner-html__h0K4t, section[class*='job-desc'], .job-desc, .dang-inner-html",
    "apply": ["button#apply-button", "button:has-text('Apply'):not(:has-text('company site'))"],
    "company_site": "button:has-text('Apply on company site'), #company-site-button",
    "chatbot": ".chatbot_DrawerContentWrapper, [class*='chatbot_Drawer'], [class*='chatbot']",
    "bot_msg": ".botMsg, [class*='botItem'], li.botItem",
    "text_input": "div.textArea[contenteditable='true'], [contenteditable='true'], input[type='text']",
    "radio": ".ssrc__radio-btn-container, [class*='radio-btn-container'], label:has(input[type='radio'])",
    "chips": ".chatbot_Chip, [class*='chip']",
    "send": [".sendMsg", "div.send", "button:has-text('Save')", "[class*='sendMsg']"],
}
SUCCESS_TEXT = ["successfully applied", "you have applied", "applied successfully", "application sent"]
# Optional chatbot questions offer a "Skip" chip; using it is safer than guessing an answer.
SKIP_RE = re.compile(r"^\s*skip\b", re.I)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Naukri(Platform):
    name = "naukri"
    profile = "naukri"
    login_url = "https://www.naukri.com/nlogin/login"

    def is_logged_in(self, page: "Page") -> bool:
        page.goto("https://www.naukri.com/mnjuser/homepage", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        return "nlogin" not in page.url and "login" not in page.url

    def discover(self, page: "Page | None") -> Iterator[JobPosting]:
        assert page is not None
        if not self.is_logged_in(page):
            raise RuntimeError("Not logged in to Naukri. Run: python -m jobagent login naukri")
        prefs = self.settings.preferences
        exp = prefs.get("experience_years")
        age = self.config.get("job_age_days", 7)
        seen: set[str] = set()
        for role in prefs.get("roles", []):
            for loc in prefs.get("locations", []) or [""]:
                base = f"https://www.naukri.com/{_slug(role)}-jobs"
                query = [f"jobAge={age}"]
                if exp is not None:
                    query.append(f"experience={exp}")
                if loc.lower() == "remote":
                    query.append("wfhType=2")
                elif loc:
                    base += f"-in-{_slug(loc)}"
                for page_no in range(1, int(self.config.get("max_pages", 2)) + 1):
                    url = (base if page_no == 1 else f"{base}-{page_no}") + "?" + "&".join(query)
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                    if captcha_present(page):
                        wait_for_human(page, "Naukri security check", lambda: not captcha_present(page), self.headless)
                    cards = page.locator(SEL["card"])
                    count = cards.count()
                    log.info("Naukri %s @ %s page %d: %d jobs", role, loc, page_no, count)
                    if count == 0:
                        break
                    for i in range(count):
                        job = self._parse_card(cards.nth(i))
                        if job and job.external_id not in seen:
                            seen.add(job.external_id)
                            yield job

    def _parse_card(self, card: "Locator") -> JobPosting | None:
        try:
            link = card.locator(SEL["card_title"]).first
            href = link.get_attribute("href") or ""
            title = (link.get_attribute("title") or link.inner_text(timeout=1000)).strip()
            job_id = card.get_attribute("data-job-id")
            if not job_id:
                m = re.search(r"-(\d{9,})(?:\?|$)", href)
                job_id = m.group(1) if m else href
            if not href or not title:
                return None

            def txt(sel: str) -> str:
                loc = card.locator(sel).first
                return loc.inner_text(timeout=800).strip() if loc.count() else ""

            exp = txt(SEL["card_exp"])
            return JobPosting(
                platform=self.name,
                external_id=job_id,
                url=href.split("?")[0],
                title=title,
                company=txt(SEL["card_company"]),
                location=txt(SEL["card_location"]),
                # Card experience ("2-5 Yrs") helps the matcher before the full description is fetched.
                description=f"Experience: {exp}" if exp else "",
            )
        except Exception as exc:
            log.debug("card parse failed: %s", exc)
            return None

    def fetch_description(self, page: "Page", job: sqlite3.Row) -> str | None:
        page.goto(job["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        try:
            return page.locator(SEL["description"]).first.inner_text(timeout=5000)
        except Exception:
            return None

    def apply(self, page: "Page", job: sqlite3.Row, answerer: Answerer, dry_run: bool) -> ApplyResult:
        if job["url"] not in page.url:
            page.goto(job["url"], wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
        if page.locator("button:has-text('Applied')").count() or body_contains(page, ["already applied"]):
            return ApplyResult("applied", "Already applied earlier")
        if page.locator(SEL["company_site"]).count():
            return ApplyResult("needs_attention", "Apply on company site (external)")
        if dry_run:
            return ApplyResult("dry_run", "Apply button found, not clicked")
        if not click_first(page, SEL["apply"], timeout=4000):
            return ApplyResult("needs_attention", "Apply button not found")
        page.wait_for_timeout(3000)

        if body_contains(page, SUCCESS_TEXT):
            return ApplyResult("applied")
        if page.locator(SEL["chatbot"]).count():
            return self._chatbot(page, answerer)
        if captcha_present(page):
            if wait_for_human(page, "Naukri security check", lambda: body_contains(page, SUCCESS_TEXT), self.headless):
                return ApplyResult("applied")
            return ApplyResult("needs_attention", "CAPTCHA required")
        return ApplyResult("needs_attention", "No confirmation after clicking Apply")

    def _skip(self, bot: "Locator") -> bool:
        """Click the "Skip" chip if this question is optional. Returns True if clicked."""
        chips = bot.locator(SEL["chips"])
        for i in range(chips.count()):
            chip = chips.nth(i)
            if SKIP_RE.match(chip.inner_text(timeout=1000) or ""):
                chip.click()
                log.info("  skipped optional chatbot question")
                return True
        return False

    def _chatbot(self, page: "Page", answerer: Answerer) -> ApplyResult:
        bot = page.locator(SEL["chatbot"]).first
        last_question = ""
        for _ in range(20):
            page.wait_for_timeout(1500)
            if body_contains(page, SUCCESS_TEXT):
                return ApplyResult("applied", "Answered screening questions")
            msgs = [m.strip() for m in bot.locator(SEL["bot_msg"]).all_inner_texts() if m.strip()]
            if not msgs:
                return ApplyResult("needs_attention", "Chatbot opened but no question found")
            question = msgs[-1]
            if question == last_question:
                return ApplyResult("needs_attention", f"Stuck on question: {question[:120]}")
            last_question = question

            radios = bot.locator(SEL["radio"])
            chips = bot.locator(SEL["chips"])
            if radios.count():
                options = [t.strip() for t in radios.all_inner_texts()]
                ans = answerer.answer(Question(question, "radio", options, True))
                if ans is None:
                    if self._skip(bot):
                        continue
                    return ApplyResult("needs_attention", f"Unanswered: {question[:120]}")
                radios.nth(options.index(ans)).click()
            elif chips.count():
                options = [t.strip() for t in chips.all_inner_texts()]
                answerable = [o for o in options if not SKIP_RE.match(o)]
                ans = answerer.answer(Question(question, "select", answerable, True))
                if ans is None:
                    if self._skip(bot):
                        continue
                    return ApplyResult("needs_attention", f"Unanswered: {question[:120]}")
                chips.nth(options.index(ans)).click()
            else:
                ans = answerer.answer(Question(question, "text", None, True))
                if ans is None:
                    if self._skip(bot):
                        continue
                    return ApplyResult("needs_attention", f"Unanswered: {question[:120]}")
                box = bot.locator(SEL["text_input"]).last
                box.click()
                if box.evaluate("e => e.tagName") == "INPUT":
                    box.fill(ans)
                else:  # contenteditable div
                    page.keyboard.type(ans, delay=30)
            click_first(bot, SEL["send"], timeout=2000)
        text = page_text(page, 2000)
        return ApplyResult("needs_attention", "Chatbot did not finish. Last screen: " + text[:150])
