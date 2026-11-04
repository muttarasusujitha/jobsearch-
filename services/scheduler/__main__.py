"""Scheduler service: publishes commands on a timer, using [schedule] in the config.

    [schedule]
    discover_every_minutes = 180
    apply_every_minutes = 120
    track_every_minutes = 60
    find_startups_every_hours = 24
    run_on_start = false

Set any value to 0 to turn that job off.
"""

from __future__ import annotations

import logging
import time

from jobagent.bus import from_env

from ..common import run_forever, settings_or_wait, setup_logging, stop_event
from .logic import JOBS, due_jobs

setup_logging("scheduler")
log = logging.getLogger("scheduler")


def main() -> None:
    stop = stop_event()
    bus = from_env()
    settings = settings_or_wait()
    start = time.time()
    run_on_start = bool(settings.section("schedule").get("run_on_start", False))
    last_run: dict[str, float] = {} if run_on_start else {stream: start for *_, stream in JOBS}

    def loop() -> None:
        while not stop.is_set():
            schedule = settings_or_wait().section("schedule")
            now = time.time()
            for stream in due_jobs(schedule, last_run, now):
                bus.publish(stream, {})
                last_run[stream] = now
                log.info("Published %s", stream)
            stop.wait(30)

    run_forever(loop, stop, "scheduler")


main()
