"""Small HTTP client the services and the dashboard use to talk to the API service."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ApiError(Exception):
    pass


class ApiClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int = 30):
        self.base = (base_url or os.environ.get("JOBAGENT_API_URL", "http://localhost:8000")).rstrip("/")
        self.token = token if token is not None else os.environ.get("JOBAGENT_API_TOKEN", "")
        self.timeout = timeout

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        url = self.base + path
        if params:
            clean = {k: (",".join(map(str, v)) if isinstance(v, (list, tuple)) else v) for k, v in params.items() if v is not None}
            url += "?" + urllib.parse.urlencode(clean)
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise ApiError(f"{method} {path} -> HTTP {exc.code}: {exc.read()[:300]!r}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"API not reachable at {self.base}: {exc.reason}") from exc

    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params)

    def post(self, path: str, body: Any = None) -> Any:
        return self._request("POST", path, body=body if body is not None else {})

    # Convenience wrappers
    def jobs(self, status: list[str] | None = None, platform: str | None = None, limit: int = 5000) -> list[dict]:
        return self.get("/jobs", status=status, platform=platform, limit=limit)

    def applied_count(self, since: str, platform: str | None = None) -> int:
        return int(self.get("/stats/applied", since=since, platform=platform)["count"])

    def companies(self, found_only: bool = True) -> list[dict]:
        return self.get("/companies", found_only=str(found_only).lower())

    def company_candidates(self, cities: list[str]) -> list[dict]:
        return self.get("/companies/candidates", cities=cities)

    def recently_checked(self) -> list[str]:
        return self.get("/companies/checked")
