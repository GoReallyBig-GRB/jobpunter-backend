from datetime import datetime, timezone

import pytest

from app.application.jobs.schedule_window import format_clock, in_window, next_window_open, parse_clock, validate_window


def utc(h, m=0, day=2):
    return datetime(2026, 11, day, h, m, tzinfo=timezone.utc)


@pytest.mark.parametrize("value,expected", [("00:00", 0), ("09:30", 570), ("23:59", 1439)])
def test_parse_clock(value, expected):
    assert parse_clock(value) == expected
    assert format_clock(expected) == value


@pytest.mark.parametrize("value", ["24:00", "9:30", "12:60", "ab:cd", "", "12-30"])
def test_parse_clock_rejects_malformed(value):
    with pytest.raises(ValueError):
        parse_clock(value)


def test_validate_window_rejects_equal_bounds_and_unknown_zone():
    with pytest.raises(ValueError):
        validate_window(60, 60, "UTC")
    with pytest.raises(ValueError):
        validate_window(60, 120, "Not/AZone")
    validate_window(60, 120, "Africa/Lagos")


@pytest.mark.parametrize("hour,expected", [(8, False), (9, True), (16, True), (17, False)])
def test_same_day_window_is_start_inclusive_end_exclusive(hour, expected):
    assert in_window(utc(hour), 9 * 60, 17 * 60, "UTC") is expected


@pytest.mark.parametrize("hour,expected", [(21, False), (22, True), (23, True), (0, True), (1, True), (2, False)])
def test_window_crossing_midnight(hour, expected):
    assert in_window(utc(hour, day=3 if hour < 12 else 2), 22 * 60, 2 * 60, "UTC") is expected


def test_next_window_open():
    assert next_window_open(utc(12), 22 * 60, 2 * 60, "UTC") == utc(22)
    assert next_window_open(utc(23), 22 * 60, 2 * 60, "UTC") == utc(23)  # already open
    assert next_window_open(utc(18), 9 * 60, 17 * 60, "UTC") == utc(9, day=3)  # tomorrow
    # 09:00 Lagos is 08:00 UTC.
    assert next_window_open(utc(20), 9 * 60, 17 * 60, "Africa/Lagos") == utc(8, day=3)
