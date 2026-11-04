from __future__ import annotations

from jobagent import events

JOBS = [
    # (config key, multiplier to seconds, default, stream)
    ("discover_every_minutes", 60, 180, events.CMD_DISCOVER),
    ("apply_every_minutes", 60, 120, events.CMD_APPLY),
    ("track_every_minutes", 60, 60, events.CMD_TRACK),
    ("find_startups_every_hours", 3600, 24, events.CMD_FIND_STARTUPS),
]


def due_jobs(schedule: dict, last_run: dict[str, float], now: float) -> list[str]:
    """Streams whose interval has passed since they last ran."""
    due = []
    for key, mult, default, stream in JOBS:
        every = float(schedule.get(key, default)) * mult
        if every <= 0:
            continue
        if now - last_run.get(stream, 0.0) >= every:
            due.append(stream)
    return due
