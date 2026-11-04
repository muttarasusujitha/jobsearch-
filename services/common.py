"""Helpers shared by every service."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Callable

from jobagent.config import ConfigError, Settings, load_settings

log = logging.getLogger("services")


def setup_logging(name: str) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format=f"%(asctime)s {name:<10} %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def settings_or_wait() -> Settings:
    """Load the config, waiting (instead of crash-looping) until it exists."""
    while True:
        try:
            return load_settings()
        except ConfigError as exc:
            log.warning("%s Retrying in 30s.", exc)
            time.sleep(30)


def stop_event() -> threading.Event:
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except ValueError:  # not in main thread
            pass
    return stop


def run_forever(fn: Callable[[], None], stop: threading.Event, what: str) -> None:
    """Run `fn`, restarting it with a back-off if it fails (e.g. Redis not up yet)."""
    delay = 2
    while not stop.is_set():
        try:
            fn()
            return
        except Exception as exc:
            log.error("%s failed: %s. Restarting in %ss.", what, exc, delay)
            stop.wait(delay)
            delay = min(delay * 2, 60)
