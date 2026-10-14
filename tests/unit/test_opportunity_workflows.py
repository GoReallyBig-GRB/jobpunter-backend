from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.application.opportunities.schemas import ApplicationTransitionRequest
from app.domain.opportunities.service import qualify_punt, transition_application


def test_only_current_apply_evaluation_without_blockers_can_create_punt():
    good = SimpleNamespace(
        validity_status="CURRENT",
        outcome="APPLY",
        score=85,
        hard_blockers=[],
        evidence_summary={"dimensions": {
            "deterministic_eligibility": "ELIGIBLE",
            "mandatory_requirements": "MET",
            "application_accessibility": "VERIFIED_ACTIVE",
            "job_freshness": "FRESH",
        }},
    )
    qualify_punt(good)
    for bad in [
        SimpleNamespace(validity_status="STALE", outcome="APPLY", hard_blockers=[]),
        SimpleNamespace(validity_status="CURRENT", outcome="REVIEW", hard_blockers=[]),
        SimpleNamespace(validity_status="CURRENT", outcome="WATCH", hard_blockers=[]),
        SimpleNamespace(validity_status="CURRENT", outcome="REJECT", hard_blockers=[]),
        SimpleNamespace(validity_status="CURRENT", outcome="APPLY", hard_blockers=["visa"]),
    ]:
        with pytest.raises(ValueError):
            qualify_punt(bad)


def test_application_submission_requires_candidate_action_and_approved_state():
    now = datetime.now(timezone.utc)
    app = SimpleNamespace(status="APPROVED", revision=1, updated_at=None, submitted_at=None)
    with pytest.raises(ValueError, match="authenticated user action"):
        transition_application(app, "SUBMITTED", actor_type="SYSTEM", reason="system submission", now=now)
    event = transition_application(app, "SUBMITTED", actor_type="USER", reason="Candidate submitted application", now=now)
    assert app.status == "SUBMITTED"
    assert app.revision == 2
    assert app.submitted_at == now
    assert event["from_status"] == "APPROVED"


def test_application_transition_schema_rejects_noncanonical_states():
    request = ApplicationTransitionRequest(
        target_status="APPROVED",
        expected_revision=1,
        reason="Candidate approved the package",
    )
    assert request.target_status == "APPROVED"

    with pytest.raises(ValidationError):
        ApplicationTransitionRequest(
            target_status="SCREENING",
            expected_revision=1,
            reason="Candidate records screening",
        )


def test_application_transition_requires_a_reason_before_mutation():
    app = SimpleNamespace(status="PLANNED", revision=1, updated_at=None, submitted_at=None)
    with pytest.raises(ValueError, match="reason"):
        transition_application(app, "WITHDRAWN", actor_type="USER", reason="  ")
    assert app.status == "PLANNED"
    assert app.revision == 1


def test_terminal_application_states_cannot_be_reopened():
    app = SimpleNamespace(status="REJECTED", revision=2, updated_at=None, submitted_at=None)
    with pytest.raises(ValueError, match="Invalid application transition"):
        transition_application(app, "INTERVIEW", actor_type="USER", reason="Interview scheduled")



@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"score": None}, "scored evaluation"),
        ({"evidence_summary": {"dimensions": {"deterministic_eligibility": "UNKNOWN"}}}, "deterministic eligibility"),
        ({"evidence_summary": {"dimensions": {
            "deterministic_eligibility": "ELIGIBLE",
            "mandatory_requirements": "UNMET",
            "application_accessibility": "VERIFIED_ACTIVE",
            "job_freshness": "FRESH",
        }}}, "mandatory requirements"),
        ({"evidence_summary": {"dimensions": {
            "deterministic_eligibility": "ELIGIBLE",
            "mandatory_requirements": "MET",
            "application_accessibility": "UNKNOWN",
            "job_freshness": "FRESH",
        }}}, "application route"),
        ({"evidence_summary": {"dimensions": {
            "deterministic_eligibility": "ELIGIBLE",
            "mandatory_requirements": "MET",
            "application_accessibility": "VERIFIED_ACTIVE",
            "job_freshness": "STALE",
        }}}, "freshness status"),
    ],
)
def test_punt_qualification_requires_separate_quality_gates(change, message):
    good = {
        "validity_status": "CURRENT",
        "outcome": "APPLY",
        "score": 85,
        "hard_blockers": [],
        "evidence_summary": {"dimensions": {
            "deterministic_eligibility": "ELIGIBLE",
            "mandatory_requirements": "MET",
            "application_accessibility": "VERIFIED_ACTIVE",
            "job_freshness": "FRESH",
        }},
    }
    good.update(change)
    with pytest.raises(ValueError, match=message):
        qualify_punt(SimpleNamespace(**good))
