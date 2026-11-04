"""Tiny stdlib HTTP helper for public job-board APIs (Greenhouse, Lever, Ashby, Workday)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "job-agent/0.1 (personal job search tool)"


def _request(req: urllib.request.Request, retries: int, timeout: int) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 404, 410, 422):
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def get_text(url: str, retries: int = 2, timeout: int = 30) -> str:
    """GET an HTML page and return it as text."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 404, 410):
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def get_json(url: str, retries: int = 2, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    return _request(req, retries, timeout)


def post_json(url: str, payload: dict[str, Any], retries: int = 2, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"},
    )
    return _request(req, retries, timeout)
