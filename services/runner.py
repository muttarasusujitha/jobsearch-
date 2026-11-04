"""Starts one worker service: python -m services.<name>"""

from __future__ import annotations

import logging
import os

from jobagent import events
from jobagent.apiclient import ApiClient
from jobagent.bus import from_env

from .common import run_forever, settings_or_wait, setup_logging, stop_event
from .workers import Browser, Discovery, Matcher, StartupFinder, Tracker

# service name -> (streams it listens to, factory)
SERVICES = {
    "discovery": ([events.CMD_DISCOVER], lambda bus, api, s: Discovery(bus, api, s, uses_browser=False)),
    "browser": (
        [events.CMD_DISCOVER, events.CMD_APPLY],
        lambda bus, api, s: Browser(bus, api, s, headless=os.environ.get("BROWSER_HEADLESS", "false").lower() == "true"),
    ),
    "matcher": ([events.JOB_NEW], lambda bus, api, s: Matcher(bus, s)),
    "tracker": ([events.CMD_TRACK], lambda bus, api, s: Tracker(bus, s)),
    "startups": ([events.CMD_FIND_STARTUPS], lambda bus, api, s: StartupFinder(bus, api, s)),
}


def main(name: str) -> None:
    setup_logging(name)
    log = logging.getLogger(name)
    streams, factory = SERVICES[name]
    stop = stop_event()
    settings_or_wait()  # don't start until the config exists
    bus = from_env()
    worker = factory(bus, ApiClient(), settings_or_wait)

    def handle(stream: str, payload: dict) -> None:
        log.log(logging.DEBUG if stream.startswith("evt.") else logging.INFO, "Received %s %s", stream, payload if stream.startswith("cmd.") else "")
        worker.handle(stream, payload)

    run_forever(lambda: bus.consume(streams, name, handle, stop=stop), stop, name)
