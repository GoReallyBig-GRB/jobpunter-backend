import pytest

from app.application.operations.worker_heartbeat import safe_worker_metrics


def test_worker_metrics_allow_only_small_scalar_operational_values():
    assert safe_worker_metrics({
        "claimed": 4,
        "processed": 3,
        "cycle_name": "idle",
        "healthy": True,
        "email": "candidate@example.test",
        "message_body": "private",
        "api_token": "secret",
        "candidate_count": 20,
        "payload": {"notification_id": "secret"},
        "unsafe key": 1,
        "long_value": "x" * 65,
    }) == {
        "claimed": 4,
        "processed": 3,
        "cycle_name": "idle",
        "healthy": True,
    }


def test_worker_metrics_reject_non_object_values():
    assert safe_worker_metrics(None) == {}
    assert safe_worker_metrics([]) == {}


def test_existing_worker_heartbeat_updates_safe_metrics_and_success_time():
    from datetime import datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from app.application.operations import worker_heartbeat as hb

    row = SimpleNamespace(
        status="IDLE", last_seen_at=None, last_success_at=None,
        last_error_category=None, metrics={},
    )

    class Session:
        def scalar(self, _stmt):
            return row

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    hb.record_worker_heartbeat(
        Session,
        worker_name="outbox",
        instance_id=uuid4(),
        status="RUNNING",
        metrics={"claimed": 2, "email": "x@example.test"},
        successful_cycle=True,
    )
    assert row.metrics == {"claimed": 2}
    assert row.status == "RUNNING"
    assert isinstance(row.last_success_at, datetime) and row.last_success_at.tzinfo
