from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.jobs.discovery_missions import (
    policy_snapshot,
    validate_discovery_settings,
)
from app.workers import discovery_scheduler
from app.workers.discovery_mission_worker import TERMINAL_OUTBOX_STATES, _raw_records_resolved


def test_discovery_settings_validation_rejects_invalid_cadence_and_filters():
    with pytest.raises(ValueError, match="cadence_minutes"):
        validate_discovery_settings(
            status="ENABLED",
            cadence_minutes=0,
            target_opportunities=20,
            max_job_age_days=21,
            evaluation_depth="preliminary",
            source_filters={},
        )
    with pytest.raises(ValueError, match="source_filters"):
        validate_discovery_settings(
            status="ENABLED",
            cadence_minutes=60,
            target_opportunities=20,
            max_job_age_days=21,
            evaluation_depth="preliminary",
            source_filters=[],
        )


def test_default_policy_snapshot_preserves_discovery_wave_order():
    snapshot = policy_snapshot(None)
    assert snapshot["target_opportunities"] == 20
    assert snapshot["verification_required"] is True
    assert [wave["key"] for wave in snapshot["waves"]] == [
        "official_feeds",
        "jobspy",
        "targeted_scrapers",
        "browser_automation",
    ]


def test_settings_policy_snapshot_is_a_copy_of_candidate_configuration():
    settings = SimpleNamespace(
        status="ENABLED",
        cadence_minutes=60,
        target_opportunities=12,
        max_job_age_days=14,
        verification_required=False,
        evaluation_depth="full",
        source_filters={"source_ids": [str(uuid4())]},
    )
    snapshot = policy_snapshot(settings)
    assert snapshot["target_opportunities"] == 12
    assert snapshot["max_job_age_days"] == 14
    assert snapshot["verification_required"] is False
    assert snapshot["evaluation_depth"] == "full"
    assert snapshot["source_filters"] == settings.source_filters


def test_scheduler_once_runs_one_cycle_without_sleep(monkeypatch):
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    cycles = []
    monkeypatch.setattr(discovery_scheduler, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        discovery_scheduler,
        "schedule_due_missions",
        lambda db, limit: cycles.append(limit) or [uuid4()],
    )
    sleeps = []
    count = discovery_scheduler.run_scheduler(
        once=True,
        batch_size=3,
        should_stop=lambda: False,
        sleep=sleeps.append,
    )
    assert count == 1
    assert cycles == [3]
    assert sleeps == []


@pytest.mark.parametrize(
    "source_filters",
    [
        {"source_ids": "not-a-list"},
        {"source_types": [""]},
        {"connector_types": [1]},
        {"acquisition_context": []},
        {"unrecognized_filter": True},
    ],
)
def test_discovery_settings_reject_malformed_or_unknown_source_filters(source_filters):
    with pytest.raises(ValueError, match="source_filters"):
        validate_discovery_settings(
            status="ENABLED",
            cadence_minutes=60,
            target_opportunities=20,
            max_job_age_days=21,
            evaluation_depth="preliminary",
            source_filters=source_filters,
        )



def test_failed_outbox_event_is_terminal_for_discovery_canonicalization():
    raw_record_id = uuid4()
    run_id = uuid4()

    class Rows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class FakeDb:
        def __init__(self):
            self.calls = 0

        def scalars(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return Rows([raw_record_id])
            if self.calls == 2:
                return Rows([])
            if self.calls == 3:
                return Rows([
                    SimpleNamespace(aggregate_id=raw_record_id, status="FAILED")
                ])
            raise AssertionError("unexpected database query")

    assert "FAILED" in TERMINAL_OUTBOX_STATES
    assert _raw_records_resolved(FakeDb(), [run_id]) is True
