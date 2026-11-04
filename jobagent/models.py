from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class JobPosting:
    platform: str
    external_id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    posted_at: str | None = None
    apply_url: str | None = None


ApplyStatus = Literal["applied", "failed", "needs_attention", "dry_run"]


@dataclass
class ApplyResult:
    status: ApplyStatus
    note: str = ""
