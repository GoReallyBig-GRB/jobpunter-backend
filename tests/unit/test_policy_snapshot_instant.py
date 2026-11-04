from datetime import datetime, timedelta, timezone

from app.application.jobs.registered_acquisition import _utc_instant_text


def test_same_instant_renders_identically_across_session_time_zones():
    utc = datetime(2026, 10, 28, 22, 9, 24, 162028, tzinfo=timezone.utc)
    lagos = utc.astimezone(timezone(timedelta(hours=1)))
    assert _utc_instant_text(utc) == _utc_instant_text(lagos) == "2026-10-28T22:09:24.162028+00:00"


def test_naive_values_are_treated_as_utc_and_none_is_preserved():
    assert _utc_instant_text(datetime(2026, 1, 1, 0, 0, 0)) == "2026-01-01T00:00:00+00:00"
    assert _utc_instant_text(None) is None
