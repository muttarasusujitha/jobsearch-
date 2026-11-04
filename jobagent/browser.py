"""Playwright browser sessions and a generic, rule-based form filler.

Each platform gets its own persistent browser profile in data/browser/<name>, so you
log in once by hand (`python -m jobagent login linkedin`) and the cookies are reused.
Passwords are never stored by this tool.

This module does not try to hide automation or solve CAPTCHAs. When a CAPTCHA or
verification step appears, it waits for you to handle it in the visible browser
(or gives up and marks the job "Needs attention" in headless mode).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .answers import Answerer, Question
from .config import DATA_DIR

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Locator, Page

log = logging.getLogger(__name__)

CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='hcaptcha.com'][src*='challenge']",
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='captcha-delivery.com']",
    "#captcha-internal",
    "[id*='captcha' i]:visible",
]
CAPTCHA_TEXT = [
    "verify you are human",
    "verify you're human",
    "are you a robot",
    "security verification",
    "let's do a quick security check",
    "unusual activity",
]


class BrowserSession:
    """Context manager around a persistent Chromium profile."""

    def __init__(self, profile: str, headless: bool = False, slow_mo: int = 50):
        self.profile_dir = DATA_DIR / "browser" / profile
        self.headless = headless
        self.slow_mo = slow_mo
        self._pw: Any = None
        self.context: BrowserContext | None = None

    def __enter__(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "Playwright is not installed. Run: pip install -r requirements.txt && playwright install chromium"
            ) from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        context = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            slow_mo=self.slow_mo,
            viewport={"width": 1366, "height": 900},
        )
        context.set_default_timeout(15000)
        self.context = context
        return self

    def new_page(self) -> "Page":
        assert self.context is not None
        if self.context.pages:
            page = self.context.pages[0]
        else:
            page = self.context.new_page()
        return page

    def __exit__(self, *exc: object) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()


def captcha_present(page: "Page") -> bool:
    for sel in CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    try:
        body = page.locator("body").inner_text(timeout=2000).lower()[:5000]
    except Exception:
        return False
    return any(t in body for t in CAPTCHA_TEXT)


def wait_for_human(page: "Page", reason: str, done: Callable[[], bool], headless: bool, timeout_s: int = 180) -> bool:
    """Ask the user to finish a step (CAPTCHA, email code, login) in the visible browser.

    Returns True once `done()` is true, False on timeout or in headless mode.
    """
    if headless:
        return False
    print(f"\n>>> ACTION NEEDED: {reason}\n>>> Finish it in the browser window. Waiting up to {timeout_s}s...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if done():
                print(">>> Thanks, continuing.")
                return True
        except Exception:
            pass
        time.sleep(2)
    print(">>> Timed out waiting.")
    return False


def page_text(page_or_locator: "Page | Locator", limit: int = 20000) -> str:
    try:
        target = page_or_locator.locator("body") if hasattr(page_or_locator, "goto") else page_or_locator
        return target.inner_text(timeout=5000)[:limit]  # type: ignore[union-attr]
    except Exception:
        return ""


def body_contains(page: "Page", phrases: list[str]) -> bool:
    text = page_text(page).lower()
    return any(p.lower() in text for p in phrases)


def click_first(scope: "Page | Locator", selectors: list[str], timeout: int = 3000) -> bool:
    for sel in selectors:
        try:
            loc = scope.locator(sel).first
            if loc.is_visible(timeout=timeout):
                loc.click()
                return True
        except Exception:
            continue
    return False


def scroll_element(page: "Page", selector: str, steps: int = 8) -> None:
    for _ in range(steps):
        try:
            page.eval_on_selector(selector, "el => el.scrollBy(0, el.clientHeight)")
        except Exception:
            page.mouse.wheel(0, 900)
        page.wait_for_timeout(500)


# --------------------------------------------------------------------------- #
# Generic form filler
# --------------------------------------------------------------------------- #

_COLLECT_JS = r"""
(root) => {
  const scope = root || document;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const txt = el => el ? clean(el.innerText || el.textContent) : '';
  const isVisible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const byLabelFor = el => {
    if (!el.id) return '';
    const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return txt(l);
  };
  const labelOf = el => {
    const aria = el.getAttribute('aria-label');
    if (aria && clean(aria).length > 1) return clean(aria);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = clean(lb.split(/\s+/).map(id => txt(document.getElementById(id))).join(' '));
      if (t) return t;
    }
    const f = byLabelFor(el);
    if (f) return f;
    const wrap = el.closest('label');
    if (wrap && txt(wrap)) return txt(wrap);
    let p = el.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      const l = p.querySelector('label, legend, [class*="label" i], [class*="question" i]');
      if (l && txt(l) && !l.contains(el)) return txt(l);
    }
    return clean(el.getAttribute('placeholder') || el.getAttribute('name') || '');
  };
  const groupLabel = el => {
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector('legend');
      if (lg && txt(lg)) return txt(lg);
    }
    const rg = el.closest('[role="radiogroup"], [role="group"]');
    if (rg) {
      const a = rg.getAttribute('aria-label');
      if (a) return clean(a);
      const lb = rg.getAttribute('aria-labelledby');
      if (lb) { const t = txt(document.getElementById(lb)); if (t) return t; }
    }
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const radios = p.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`);
      if (radios.length > 1) {
        const l = p.querySelector('legend, label:not(:has(input)), [class*="label" i], [class*="question" i]');
        if (l && !Array.from(radios).some(r => l.contains(r)) && txt(l)) return txt(l);
      }
    }
    return el.name || '';
  };
  const isRequired = (el, label) =>
    el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label) || /\brequired\b/i.test(label);

  document.querySelectorAll('[data-jobagent-idx]').forEach(e => e.removeAttribute('data-jobagent-idx'));
  const out = [];
  let n = 0;
  const tag = el => { const id = String(n++); el.setAttribute('data-jobagent-idx', id); return id; };
  const radioGroups = {};

  scope.querySelectorAll('input, textarea, select').forEach(el => {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) return;
    if (el.disabled || el.readOnly) return;
    const hiddenAllowed = ['radio', 'checkbox', 'file'].includes(type);
    if (!hiddenAllowed && !isVisible(el)) return;

    if (type === 'radio') {
      const key = el.name || ('radio-' + n);
      if (!radioGroups[key]) {
        radioGroups[key] = { kind: 'radio', label: groupLabel(el), options: [], idxs: [], required: false, value: '' };
        out.push(radioGroups[key]);
      }
      const g = radioGroups[key];
      g.idxs.push(tag(el));
      g.options.push(byLabelFor(el) || txt(el.closest('label')) || el.value);
      if (el.checked) g.value = el.value || 'checked';
      if (el.required || el.getAttribute('aria-required') === 'true') g.required = true;
      if (/\*\s*$/.test(g.label)) g.required = true;
      return;
    }

    const label = labelOf(el);
    const item = { idx: tag(el), label, required: isRequired(el, label), name: el.name || '', options: null };
    if (el.tagName === 'SELECT') {
      item.kind = 'select';
      item.options = Array.from(el.options).map(o => clean(o.textContent));
      const sel = el.options[el.selectedIndex];
      item.value = sel && sel.value && !/^(select|choose|--)/i.test(clean(sel.textContent)) ? clean(sel.textContent) : '';
    } else if (type === 'checkbox') {
      item.kind = 'checkbox';
      item.value = el.checked ? 'yes' : '';
    } else if (type === 'file') {
      item.kind = 'file';
      item.value = el.files && el.files.length ? 'file' : '';
    } else if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete')) {
      item.kind = 'combobox';
      item.value = el.value || '';
      const container = el.closest('[class*="select" i], [class*="container" i]');
      const shown = container ? container.querySelector('[class*="single-value" i], [class*="singleValue" i]') : null;
      if (shown && txt(shown)) item.value = txt(shown);
    } else {
      item.kind = el.tagName === 'TEXTAREA' ? 'textarea' : (type === 'number' ? 'number' : 'text');
      item.value = el.value || '';
    }
    out.push(item);
  });

  // Dropdowns built from buttons (Workday): <button aria-haspopup="listbox">Select One</button>
  scope.querySelectorAll('button[aria-haspopup="listbox"]').forEach(el => {
    if (el.disabled || !isVisible(el)) return;
    const label = labelOf(el);
    const shown = clean(el.innerText);
    out.push({
      idx: tag(el), kind: 'listbox', label, name: el.name || '', options: null,
      required: isRequired(el, label),
      value: (!shown || /^(select one|select|choose)/i.test(shown)) ? '' : shown,
    });
  });

  // Yes / No toggle buttons (Ashby)
  scope.querySelectorAll('button').forEach(el => {
    if (clean(el.innerText) !== 'Yes' || !isVisible(el)) return;
    const parent = el.parentElement;
    const no = parent && Array.from(parent.querySelectorAll('button')).find(b => clean(b.innerText) === 'No');
    if (!no) return;
    let label = '';
    let p = parent;
    for (let i = 0; i < 4 && p && !label; i++, p = p.parentElement) {
      const l = p.querySelector('label, legend, [class*="title" i], [class*="question" i]');
      if (l && !l.contains(el)) label = txt(l);
    }
    const pressed = [el, no].some(b => b.getAttribute('aria-pressed') === 'true' || /active|selected/i.test(b.className));
    out.push({
      kind: 'buttons', label, options: ['Yes', 'No'], idxs: [tag(el), tag(no)],
      required: /\*\s*$/.test(label) || /\brequired\b/i.test(label), value: pressed ? 'set' : '',
    });
  });
  return out;
}
"""


def collect_fields(scope: "Page | Locator") -> list[dict[str, Any]]:
    if hasattr(scope, "goto"):
        return scope.evaluate(_COLLECT_JS, None)  # type: ignore[union-attr]
    return scope.evaluate(_COLLECT_JS)  # type: ignore[union-attr]


def _pick_combobox(page: "Page", el: "Locator", answerer: Answerer, label: str, required: bool) -> bool:
    """Open a custom dropdown (react-select style), read options, pick the best one."""
    try:
        el.click()
        page.wait_for_timeout(600)
        options = page.locator("[role='option']")
        texts = [t.strip() for t in options.all_inner_texts()][:200]
        if texts:
            ans = answerer.answer(Question(label=label, kind="select", options=texts, required=required))
            if ans is None:
                el.press("Escape")
                return False
            options.nth(texts.index(ans)).click()
            return True
        # Typeahead with no options until you type (e.g. location fields)
        ans = answerer.answer(Question(label=label, kind="text", required=required))
        if not ans:
            return False
        el.fill(ans)
        page.wait_for_timeout(1200)
        if page.locator("[role='option']").count():
            page.locator("[role='option']").first.click()
        else:
            el.press("ArrowDown")
            el.press("Enter")
        return True
    except Exception as exc:
        log.debug("combobox %r failed: %s", label, exc)
        return False


def fill_form(
    page: "Page",
    scope: "Page | Locator",
    answerer: Answerer,
    resume_path: Path | None,
    cover_letter_path: Path | None = None,
) -> list[str]:
    """Fill every empty field in `scope`. Returns labels of required fields left unanswered."""
    unanswered: list[str] = []
    fields = collect_fields(scope)

    for f in fields:
        kind, label, required = f["kind"], f.get("label", ""), f.get("required", False)
        if f.get("value"):
            continue  # already filled (platforms often prefill from your profile)

        try:
            if kind in ("radio", "buttons"):
                ans = answerer.answer(Question(label=label, kind="radio", options=f["options"], required=required))
                if ans is None:
                    if required:
                        unanswered.append(label)
                    continue
                idx = f["idxs"][f["options"].index(ans)]
                if kind == "buttons":
                    page.locator(f"[data-jobagent-idx='{idx}']").click()
                    continue
                radio = page.locator(f"[data-jobagent-idx='{idx}']")
                try:
                    radio.check(force=True, timeout=3000)
                except Exception:
                    page.locator(f"label[for='{radio.get_attribute('id')}']").click()
                continue

            el = page.locator(f"[data-jobagent-idx='{f['idx']}']")
            label_lower = f"{label} {f.get('name', '')}".lower()

            if kind == "file":
                if any(w in label_lower for w in ("cover", "letter")):
                    if cover_letter_path and cover_letter_path.exists():
                        el.set_input_files(str(cover_letter_path))
                    elif required:
                        unanswered.append(label or "Cover letter file")
                elif resume_path and resume_path.exists():
                    el.set_input_files(str(resume_path))
                    page.wait_for_timeout(1500)
                elif required:
                    unanswered.append(label or "Resume upload")
                continue

            if kind in ("combobox", "listbox"):
                if not _pick_combobox(page, el, answerer, label, required) and required:
                    unanswered.append(label)
                continue

            ans = answerer.answer(Question(label=label, kind=kind, options=f.get("options"), required=required))
            if ans is None:
                if required:
                    unanswered.append(label)
                continue
            if kind == "select":
                el.select_option(label=ans)
            elif kind == "checkbox":
                if ans == "yes":
                    try:
                        el.check(force=True, timeout=3000)
                    except Exception:
                        page.locator(f"label[for='{el.get_attribute('id')}']").click()
                elif required:
                    unanswered.append(label)
            else:
                el.fill(ans)
        except Exception as exc:
            log.warning("Could not fill %r: %s", label, exc)
            if required:
                unanswered.append(label)
    return unanswered
