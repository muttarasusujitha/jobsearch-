"""API service: REST API for the dashboard and services, and the only writer to the database.

Run: python -m services.api   (uvicorn on 0.0.0.0:8000)
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from jobagent import db
from jobagent.bus import from_env
from jobagent.config import DEFAULT_CONFIG, ConfigError, Settings, load_settings

from ..common import run_forever, setup_logging, stop_event
from .logic import API_STREAMS, ApiLogic

setup_logging("api")
log = logging.getLogger("api")

API_TOKEN = os.environ.get("JOBAGENT_API_TOKEN", "")
_bus = None
_settings_cache: dict[str, Any] = {"mtime": None, "settings": None}
_stop = stop_event()


def bus():
    global _bus
    if _bus is None:
        _bus = from_env()
    return _bus


def current_settings() -> Settings | None:
    """Reload the config when the file changes, so edits apply without a restart."""
    try:
        mtime = DEFAULT_CONFIG.stat().st_mtime
    except FileNotFoundError:
        return None
    if mtime != _settings_cache["mtime"]:
        try:
            _settings_cache["settings"] = load_settings()
            _settings_cache["mtime"] = mtime
        except ConfigError as exc:
            log.warning("Config error: %s", exc)
    return _settings_cache["settings"]


def consume_events() -> None:
    conn = db.connect()

    def handle(stream: str, payload: dict[str, Any]) -> None:
        ApiLogic(conn, bus(), current_settings()).handle(stream, payload)

    run_forever(lambda: bus().consume(API_STREAMS, "api", handle, stop=_stop), _stop, "event consumer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect().close()  # create tables
    threading.Thread(target=consume_events, name="events", daemon=True).start()
    yield
    _stop.set()


app = FastAPI(title="Job Agent API", version="1.0", lifespan=lifespan)


def auth(authorization: str = Header(default="")) -> None:
    if API_TOKEN and not hmac.compare_digest(authorization.encode(), f"Bearer {API_TOKEN}".encode()):
        raise HTTPException(status_code=401, detail="Missing or wrong API token")


def logic() -> Iterator[ApiLogic]:
    conn = db.connect()
    try:
        yield ApiLogic(conn, bus(), current_settings())
    finally:
        conn.close()


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


class StatusChange(BaseModel):
    status: str
    note: str = ""


class RunRequest(BaseModel):
    platforms: list[str] | None = None
    dry_run: bool = False
    limit: int | None = None
    cities: list[str] | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/jobs", dependencies=[Depends(auth)])
def list_jobs(status: str | None = None, platform: str | None = None, q: str | None = None,
              limit: int = Query(5000, le=20000), description: bool = False, api: ApiLogic = Depends(logic)):
    return api.jobs(_csv(status), platform, q, limit, with_description=description)


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(job_id: int, api: ApiLogic = Depends(logic)):
    job = api.job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.post("/jobs/{job_id}/status", dependencies=[Depends(auth)])
def change_status(job_id: int, body: StatusChange, api: ApiLogic = Depends(logic)):
    try:
        return api.set_status(job_id, body.status, body.note[:500])
    except KeyError:
        raise HTTPException(404, "Job not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/events", dependencies=[Depends(auth)])
def list_events(limit: int = 300, status: str | None = None, api: ApiLogic = Depends(logic)):
    return api.events(limit, _csv(status))


@app.get("/notes", dependencies=[Depends(auth)])
def notes(ids: str = "", api: ApiLogic = Depends(logic)):
    id_list = [int(i) for i in ids.split(",") if i.strip().isdigit()][:2000]
    return {str(k): v for k, v in api.last_notes(id_list).items()}


@app.get("/stats/applied", dependencies=[Depends(auth)])
def applied(since: str, platform: str | None = None, api: ApiLogic = Depends(logic)):
    return {"count": api.applied_count(since, platform)}


@app.get("/stats/last-activity", dependencies=[Depends(auth)])
def last_activity(api: ApiLogic = Depends(logic)):
    return {"at": api.last_activity()}


@app.get("/emails", dependencies=[Depends(auth)])
def emails(limit: int = 200, api: ApiLogic = Depends(logic)):
    return api.emails(limit)


@app.get("/companies", dependencies=[Depends(auth)])
def companies(found_only: bool = False, api: ApiLogic = Depends(logic)):
    return api.companies(found_only)


@app.get("/companies/candidates", dependencies=[Depends(auth)])
def company_candidates(cities: str = "bangalore,hyderabad", api: ApiLogic = Depends(logic)):
    return db.company_candidates(api.conn, _csv(cities) or [])


@app.get("/companies/checked", dependencies=[Depends(auth)])
def companies_checked(api: ApiLogic = Depends(logic)):
    return sorted(db.recently_checked(api.conn))


@app.post("/runs/{agent}", dependencies=[Depends(auth)])
def run(agent: str, body: RunRequest | None = None, api: ApiLogic = Depends(logic)):
    try:
        return {"message": api.run(agent, (body or RunRequest()).model_dump(exclude_none=True))}
    except KeyError:
        raise HTTPException(404, f"Unknown agent {agent!r}") from None
