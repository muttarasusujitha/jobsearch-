"""Message bus between the microservices.

RedisBus uses Redis Streams with consumer groups: every service has its own group, so each
service sees every message on the streams it subscribes to, and each message is handled by
one instance of that service. Messages are acknowledged after the handler succeeds; a handler
that raises is logged and the message is copied to the `dead-letter` stream.

MemoryBus has the same interface and is used by tests and single-process runs.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from collections import defaultdict, deque
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)

Handler = Callable[[str, dict[str, Any]], None]
DEAD_LETTER = "dead-letter"


class Bus(Protocol):
    def publish(self, stream: str, payload: dict[str, Any]) -> None: ...

    def consume(self, streams: list[str], group: str, handler: Handler, stop: threading.Event | None = None) -> None: ...


class RedisBus:
    def __init__(self, url: str | None = None, maxlen: int = 20000):
        import redis  # only needed inside the services

        self.r = redis.Redis.from_url(url or os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
        self.maxlen = maxlen

    def publish(self, stream: str, payload: dict[str, Any]) -> None:
        self.r.xadd(stream, {"data": json.dumps(payload, default=str)}, maxlen=self.maxlen, approximate=True)

    def _ensure_group(self, stream: str, group: str) -> None:
        import redis

        try:
            self.r.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def consume(self, streams: list[str], group: str, handler: Handler, stop: threading.Event | None = None) -> None:
        for s in streams:
            self._ensure_group(s, group)
        consumer = f"{group}-{socket.gethostname()}-{os.getpid()}"
        # First re-deliver anything this consumer read but never acknowledged (e.g. after a crash), then new messages.
        cursors = {s: "0" for s in streams}
        log.info("%s listening on %s", group, ", ".join(streams))
        while not (stop and stop.is_set()):
            replies = self.r.xreadgroup(group, consumer, cursors, count=10, block=5000) or []
            got_new = False
            for stream, messages in replies:
                if not messages:
                    cursors[stream] = ">"
                    continue
                for msg_id, fields in messages:
                    got_new = True
                    self._handle(stream, group, msg_id, fields, handler)
            if not got_new:
                cursors = {s: ">" for s in streams}

    def _handle(self, stream: str, group: str, msg_id: str, fields: dict[str, str], handler: Handler) -> None:
        try:
            payload = json.loads(fields.get("data", "{}"))
            handler(stream, payload)
        except Exception as exc:
            log.exception("Handler failed on %s %s", stream, msg_id)
            self.r.xadd(DEAD_LETTER, {"stream": stream, "group": group, "error": str(exc)[:500], **fields}, maxlen=5000)
        finally:
            self.r.xack(stream, group, msg_id)


class MemoryBus:
    """In-process bus with the same semantics (one queue per stream per group)."""

    def __init__(self) -> None:
        self.log: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._queues: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        self._subs: dict[str, list[tuple[str, Handler]]] = defaultdict(list)

    def publish(self, stream: str, payload: dict[str, Any]) -> None:
        payload = json.loads(json.dumps(payload, default=str))  # same serialisation as Redis
        self.log[stream].append(payload)
        for group, _ in self._subs[stream]:
            self._queues[(stream, group)].append(payload)

    def subscribe(self, streams: list[str], group: str, handler: Handler) -> None:
        for s in streams:
            self._subs[s].append((group, handler))
            self._queues.setdefault((s, group), deque())

    def consume(self, streams: list[str], group: str, handler: Handler, stop: threading.Event | None = None) -> None:
        self.subscribe(streams, group, handler)
        self.run_until_idle()

    def run_until_idle(self, max_steps: int = 100000) -> int:
        steps = 0
        while steps < max_steps:
            progressed = False
            for (stream, group), q in list(self._queues.items()):
                if not q:
                    continue
                payload = q.popleft()
                for g, handler in self._subs[stream]:
                    if g == group:
                        handler(stream, payload)
                steps += 1
                progressed = True
            if not progressed:
                break
        return steps


def from_env() -> Bus:
    return RedisBus()
