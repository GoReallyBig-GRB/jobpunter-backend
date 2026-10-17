from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.candidates.evidence_library import evidence_health_status


NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def evidence(**overrides):
    values = {
        "status": "DRAFT",
        "verified_at": None,
        "expires_at": None,
        "effective_from": None,
        "effective_to": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("record", "expected_verification", "expected_health"),
    [
        (evidence(status="DRAFT"), "UNVERIFIED", "UNVERIFIED"),
        (evidence(status="PENDING_REVIEW"), "UNVERIFIED", "UNVERIFIED"),
        (evidence(status="REJECTED"), "UNVERIFIED", "REJECTED"),
        (evidence(status="ARCHIVED"), "UNVERIFIED", "ARCHIVED"),
        (evidence(status="APPROVED", verified_at=NOW), "VERIFIED", "CURRENT"),
        (evidence(status="APPROVED", verified_at=NOW, expires_at=NOW), "VERIFIED", "EXPIRED"),
        (evidence(status="APPROVED", verified_at=NOW, effective_from=NOW + timedelta(days=1)),
         "VERIFIED", "NOT_YET_EFFECTIVE"),
        (evidence(status="APPROVED", verified_at=NOW, effective_to=NOW - timedelta(seconds=1)),
         "VERIFIED", "OUT_OF_EFFECTIVE_RANGE"),
        (evidence(status="APPROVED", verified_at=NOW, effective_to=NOW),
         "VERIFIED", "OUT_OF_EFFECTIVE_RANGE"),
    ],
)
def test_evidence_health_labels_are_explicit_and_time_aware(
    record, expected_verification, expected_health
):
    assert evidence_health_status(record, NOW) == (expected_verification, expected_health)


def test_approved_status_without_verification_timestamp_is_not_reported_verified():
    record = evidence(status="APPROVED", verified_at=None)
    assert evidence_health_status(record, NOW) == ("UNVERIFIED", "UNVERIFIED")
