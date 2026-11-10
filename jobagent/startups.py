"""Startup finder: discovers which Bangalore / Hyderabad startups have a public careers
board, so their jobs can be searched automatically.

Where company names come from:
  1. config/startups.toml - a starter list of Bangalore and Hyderabad startups you can edit.
  2. Job postings already found on LinkedIn, Naukri, Wellfound etc. whose location is in
     Bangalore or Hyderabad. Every new company seen there is checked too, so the list grows
     on its own.

For each name, the finder tries likely board names on Greenhouse, Lever and Ashby (their
public job APIs) and keeps a board only if it really looks like that company: it must have
open jobs, and at least one must be in India (or, for Greenhouse, the board's display name
must match). Results, including "no board found", are cached for 14 days.
"""

from __future__ import annotations

import logging
import re
import time
import tomllib
import urllib.error
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .config import CONFIG_DIR, Settings
from .http import get_json
from .matching import city_of

log = logging.getLogger(__name__)

STARTUPS_FILE = CONFIG_DIR / "startups.toml"
INDIA_WORDS = ("india", "bangalore", "bengaluru", "hyderabad", "mumbai", "pune", "delhi", "gurgaon",
               "gurugram", "noida", "chennai", "kolkata", "ahmedabad", "kochi", "jaipur")
SUFFIXES = re.compile(r"\b(private|pvt|ltd|limited|inc|llc|llp|technologies|technology|tech|labs|india|software|solutions|ai|hq)\b\.?", re.I)


@dataclass
class Probe:
    ats: str
    slug: str
    display_name: str
    open_jobs: int
    local_jobs: int   # jobs in India
    city_jobs: int    # jobs in Bangalore or Hyderabad


def load_seeds(path: Path = STARTUPS_FILE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = []
    for city, names in (data.get("cities") or {}).items():
        for name in names:
            out.append({"name": name, "city": city.title(), "source": "seed"})
    for entry in data.get("startup", []):
        if isinstance(entry, dict) and entry.get("name"):
            out.append({"source": "seed", **entry})
    return out


def slug_candidates(name: str, extra: Iterable[str] = ()) -> list[str]:
    """Likely board names for a company: 'Urban Company' -> urbancompany, urban-company, urban."""
    base = SUFFIXES.sub(" ", name.lower())
    words = re.sub(r"[^a-z0-9 ]", " ", base).split()
    if not words:
        return list(extra)
    out = list(extra) + ["".join(words), "-".join(words)]
    if len(words) > 1:
        out.append(words[0])
    full = re.sub(r"[^a-z0-9]", "", name.lower())
    out.append(full)
    seen: list[str] = []
    for s in out:
        if s and s not in seen and len(s) >= 3:
            seen.append(s)
    return seen


def _count_locations(locations: Iterable[str]) -> tuple[int, int]:
    india = city = 0
    for loc in locations:
        low = (loc or "").lower()
        if any(w in low for w in INDIA_WORDS) or city_of(low):
            india += 1
        if city_of(low):
            city += 1
    return india, city


def _probe_greenhouse(slug: str, fetch: Callable[[str], Any]) -> Probe | None:
    meta = fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
    jobs = fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").get("jobs", [])
    india, city = _count_locations((j.get("location") or {}).get("name", "") for j in jobs)
    return Probe("greenhouse", slug, meta.get("name") or slug, len(jobs), india, city)


def _probe_lever(slug: str, fetch: Callable[[str], Any]) -> Probe | None:
    postings = fetch(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(postings, list):
        return None
    locs = []
    for p in postings:
        cats = p.get("categories") or {}
        locs.append(" ".join([cats.get("location") or "", *(cats.get("allLocations") or []), p.get("country") or ""]))
    india, city = _count_locations(locs)
    return Probe("lever", slug, slug, len(postings), india, city)


def _probe_ashby(slug: str, fetch: Callable[[str], Any]) -> Probe | None:
    jobs = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").get("jobs", [])
    locs = []
    for j in jobs:
        extra = [x.get("location", "") if isinstance(x, dict) else str(x) for x in (j.get("secondaryLocations") or [])]
        address = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
        locs.append(" ".join([j.get("location") or "", *extra, address]))
    india, city = _count_locations(locs)
    return Probe("ashby", slug, slug, len(jobs), india, city)


PROBES: dict[str, Callable[[str, Callable[[str], Any]], Probe | None]] = {
    "greenhouse": _probe_greenhouse,
    "lever": _probe_lever,
    "ashby": _probe_ashby,
}


def _name_similarity(a: str, b: str) -> float:
    na = " ".join(re.sub(r"[^a-z0-9 ]", " ", SUFFIXES.sub(" ", a.lower())).split())
    nb = " ".join(re.sub(r"[^a-z0-9 ]", " ", SUFFIXES.sub(" ", b.lower())).split())
    return SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0


def accept(name: str, probe: Probe) -> bool:
    """Does this board really belong to `name`? Guards against a same-named foreign company."""
    if probe.open_jobs == 0:
        return False
    if probe.local_jobs > 0:
        return True
    return probe.ats == "greenhouse" and _name_similarity(name, probe.display_name) >= 0.85


def find_board(
    name: str,
    slugs: Iterable[str] = (),
    ats_order: Iterable[str] = ("greenhouse", "lever", "ashby"),
    fetch: Callable[[str], Any] = get_json,
    pause: float = 0.3,
) -> Probe | None:
    """Try each ATS with each candidate slug. Returns the first accepted board, if any."""
    for slug in slug_candidates(name, slugs):
        for ats in ats_order:
            try:
                probe = PROBES[ats](slug, fetch)
            except urllib.error.HTTPError as exc:
                if exc.code not in (404, 400):
                    log.debug("%s %s: HTTP %s", ats, slug, exc.code)
                continue
            except Exception as exc:
                log.debug("%s %s failed: %s", ats, slug, exc)
                continue
            finally:
                if pause:
                    time.sleep(pause)
            if probe and accept(name, probe):
                return probe
    return None


def find_startups(
    names: list[dict[str, Any]],
    skip_keys: set[str] | None = None,
    fetch: Callable[[str], Any] = get_json,
    pause: float = 0.3,
) -> Iterator[dict[str, Any]]:
    """Yield one company record per name (with ats/slug None when no board was found)."""
    from .db import company_key, now_iso

    skip_keys = skip_keys or set()
    done: set[str] = set()
    for entry in names:
        key = company_key(entry["name"])
        if not key or key in skip_keys or key in done:
            continue
        done.add(key)
        slugs = [entry["slug"]] if entry.get("slug") else []
        order = [entry["ats"]] if entry.get("ats") in PROBES else ["greenhouse", "lever", "ashby"]
        probe = find_board(entry["name"], slugs, order, fetch=fetch, pause=pause)
        record = {
            "name": entry["name"],
            "city": entry.get("city", ""),
            "source": entry.get("source", "seed"),
            "ats": probe.ats if probe else None,
            "slug": probe.slug if probe else None,
            "open_jobs": probe.open_jobs if probe else 0,
            "local_jobs": probe.local_jobs if probe else 0,
            "checked_at": now_iso(),
        }
        if probe:
            log.info("Found %s on %s/%s (%d jobs, %d in India)", entry["name"], probe.ats, probe.slug, probe.open_jobs, probe.local_jobs)
        else:
            log.debug("No public board for %s", entry["name"])
        yield record


def merge_registry(settings: Settings, companies: Iterable[dict[str, Any]]) -> int:
    """Add boards found by the finder to the Greenhouse/Lever/Ashby lists in the settings."""
    keys = {"greenhouse": "boards", "lever": "companies", "ashby": "companies"}
    platforms = settings.raw.setdefault("platforms", {})
    enabled = platforms.setdefault("enabled", [])
    added = 0
    for c in companies:
        ats, slug = c.get("ats"), c.get("slug")
        if ats not in keys or not slug:
            continue
        section = platforms.setdefault(ats, {})
        items = section.setdefault(keys[ats], [])
        if slug not in items:
            items.append(slug)
            added += 1
        if ats not in enabled:
            enabled.append(ats)
    return added
