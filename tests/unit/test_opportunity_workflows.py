from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

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
            "application_accessibility": "VERIFIED_URL",
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
    with pytest.raises(ValueError, match="candidate"):
        transition_application(app, "SUBMITTED", actor_type="SYSTEM", now=now)
    event = transition_application(app, "SUBMITTED", actor_type="CANDIDATE", now=now)
    assert app.status == "SUBMITTED"
    assert app.revision == 2
    assert app.submitted_at == now
    assert event["from_status"] == "APPROVED"


def test_terminal_application_states_cannot_be_reopened():
    app = SimpleNamespace(status="REJECTED", revision=2, updated_at=None, submitted_at=None)
    with pytest.raises(ValueError, match="Invalid application transition"):
        transition_application(app, "INTERVIEW", actor_type="CANDIDATE")
