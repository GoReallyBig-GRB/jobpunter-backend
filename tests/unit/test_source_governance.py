import pytest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.application.jobs.source_governance import (
    SOURCE_OPERATIONAL_STATES,
    assert_automated_acquisition_permitted,
    classify_acquisition_outcome,
    validate_operational_state,
)


def test_source_permission_requires_approved_status_reference_and_timestamp():
    valid = SimpleNamespace(
        permission_review_status="APPROVED",
        permission_review_reference="policy-review://2026-10",
        permission_reviewed_at=datetime.now(timezone.utc),
        configuration={"permission_review": {
            "access_method": "PUBLIC_JOB_BOARD_API",
            "terms_reference": "https://example.com/terms",
            "rate_limit_policy": "10 requests per minute",
            "rate_limit_reference": "https://example.com/rate-limits",
        }},
    )
    assert_automated_acquisition_permitted(valid) is None

    for invalid in (
        SimpleNamespace(permission_review_status="APPROVED", permission_review_reference=None, permission_reviewed_at=datetime.now(timezone.utc)),
        SimpleNamespace(permission_review_status="PENDING", permission_review_reference="review://pending", permission_reviewed_at=datetime.now(timezone.utc)),
        SimpleNamespace(permission_review_status="APPROVED", permission_review_reference="review://missing-date", permission_reviewed_at=None),
    ):
        with pytest.raises(ValueError, match="permission review is not approved"):
            assert_automated_acquisition_permitted(invalid)


def test_operational_state_contract_accepts_only_prd_states():
    assert SOURCE_OPERATIONAL_STATES == {
        "CONFIGURED", "CHECKED", "HEALTHY", "DEGRADED", "RESTRICTED", "FAILED", "UNAVAILABLE"
    }
    assert validate_operational_state(" configured ") == "CONFIGURED"
    assert validate_operational_state(" healthy ") == "HEALTHY"
    with pytest.raises(ValueError, match="unsupported source operational state"):
        validate_operational_state("AVAILABLE")
    with pytest.raises(ValueError, match="unsupported source operational state"):
        validate_operational_state("ACTIVE")


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        ("SUCCEEDED", None, ("HEALTHY", None)),
        ("FAILED", "CONNECTOR_ERROR", ("FAILED", "CONNECTOR_ERROR")),
        ("PARTIAL", None, ("DEGRADED", "PARTIAL_ACQUISITION")),
        ("PARTIALLY_SUCCEEDED", "SELECTOR_DRIFT", ("DEGRADED", "SELECTOR_DRIFT")),
        ("RESTRICTED", None, ("RESTRICTED", "SOURCE_RESTRICTED")),
        ("UNAVAILABLE", None, ("UNAVAILABLE", "SOURCE_UNAVAILABLE")),
        ("RUNNING", None, ("CHECKED", None)),
        ("PENDING", None, ("CHECKED", None)),
    ],
)
def test_acquisition_outcome_maps_to_truthful_source_state(status, error, expected):
    assert classify_acquisition_outcome(status=status, error_category=error) == expected


@pytest.mark.parametrize(
    "missing_field",
    ["access_method", "terms_reference", "rate_limit_policy", "rate_limit_reference"],
)
def test_source_permission_requires_complete_review_evidence(missing_field):
    details = {
        "access_method": "PUBLIC_JOB_BOARD_API",
        "terms_reference": "https://example.com/terms",
        "rate_limit_policy": "10 requests per minute",
        "rate_limit_reference": "https://example.com/rate-limits",
    }
    details.pop(missing_field)
    invalid = SimpleNamespace(
        permission_review_status="APPROVED",
        permission_review_reference="policy-review://2026-10",
        permission_reviewed_at=datetime.now(timezone.utc),
        configuration={"permission_review": details},
    )
    with pytest.raises(ValueError, match="permission review is not approved"):
        assert_automated_acquisition_permitted(invalid)
