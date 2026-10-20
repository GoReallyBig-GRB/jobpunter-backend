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
