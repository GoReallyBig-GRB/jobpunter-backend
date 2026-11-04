"""Daily run windows for source refresh schedules (E4.2).

A window is [start, end) in local minutes-of-day of an IANA timezone; end < start crosses midnight.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_clock(value: str) -> int:
    """'HH:MM' -> minutes since midnight."""
    try:
        hours, minutes = value.split(":")
        h, m = int(hours), int(minutes)
    except (ValueError, AttributeError) as exc:
        raise ValueError("time must be HH:MM") from exc
    if len(value) != 5 or not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("time must be HH:MM")
    return h * 60 + m


def format_clock(minute: int | None) -> str | None:
    return None if minute is None else f"{minute // 60:02d}:{minute % 60:02d}"


def validate_window(start: int, end: int, tz_name: str) -> ZoneInfo:
    if start == end:
        raise ValueError("window start and end must differ")
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError("unknown timezone") from exc


def in_window(now: datetime, start: int, end: int, tz_name: str) -> bool:
    local = now.astimezone(ZoneInfo(tz_name))
    minute = local.hour * 60 + local.minute
    return start <= minute < end if start < end else (minute >= start or minute < end)


def next_window_open(now: datetime, start: int, end: int, tz_name: str) -> datetime:
    """The first instant at or after `now` that is inside the window (UTC)."""
    if in_window(now, start, end, tz_name):
        return now
    zone = ZoneInfo(tz_name)
    local = now.astimezone(zone)
    candidate = datetime.combine(local.date(), time(start // 60, start % 60), tzinfo=zone)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), time(start // 60, start % 60), tzinfo=zone)
    return candidate.astimezone(timezone.utc)
