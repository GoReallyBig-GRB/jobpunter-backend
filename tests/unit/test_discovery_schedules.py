from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.jobs.discovery_schedules import (
    DiscoveryScheduleConfiguration,
    next_schedule_at,
)


def _config(**changes):
    value = {
        "name": "Global sweep",
        "schedule_type": "GLOBAL_SWEEP",
        "timezone": "UTC",
        "frequency_type": "INTERVAL",
        "frequency_definition": {"interval_minutes": 30},
        "strategy_version_id": uuid4(),
        "limits": {"max_requests": 100, "max_duration_seconds": 900},
        "retry_policy": {"max_attempts": 3, "initial_backoff_seconds": 5,
                         "maximum_backoff_seconds": 60},
    }
    value.update(changes)
    return DiscoveryScheduleConfiguration.model_validate(value)


def test_interval_schedule_computes_next_utc_occurrence():
    after = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
    assert next_schedule_at(_config(), after=after) == datetime(
        2026, 11, 4, 10, 30, tzinfo=UTC
    )


def test_daily_schedule_uses_iana_timezone():
    config = _config(
        timezone="Africa/Lagos", frequency_type="DAILY",
        frequency_definition={"time": "09:15"},
    )
    after = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
    assert next_schedule_at(config, after=after) == datetime(
        2026, 11, 5, 8, 15, tzinfo=UTC
    )


@pytest.mark.parametrize("frequency_type,definition", [
    ("INTERVAL", {"time": "09:00"}),
    ("DAILY", {"interval_minutes": 5}),
    ("WEEKLY", {"time": "09:00"}),
])
def test_schedule_rejects_incomplete_or_ambiguous_frequency(frequency_type, definition):
    with pytest.raises(ValidationError):
        _config(frequency_type=frequency_type, frequency_definition=definition)


def test_source_and_target_schedules_require_explicit_scope():
    with pytest.raises(ValidationError, match="source_scope"):
        _config(schedule_type="SOURCE_REFRESH")
    with pytest.raises(ValidationError, match="target_scope"):
        _config(schedule_type="TARGET_REFRESH")


def test_schedule_rejects_unknown_timezone_and_unbounded_limits():
    with pytest.raises(ValidationError, match="IANA timezone"):
        _config(timezone="Mars/Olympus")
    with pytest.raises(ValidationError, match="At least one execution limit"):
        _config(limits={})
