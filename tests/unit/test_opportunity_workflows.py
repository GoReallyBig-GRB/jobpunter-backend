from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.opportunities.service import qualify_punt, transition_application


def test_only_current_qualified_evaluation_without_blockers_can_create_punt():
    good = SimpleNamespace(validity_status="CURRENT", outcome="QUALIFIED", hard_blockers=[])
    qualify_punt(good)
    for bad in [
        SimpleNamespace(validity_status="STALE", outcome="QUALIFIED", hard_blockers=[]),
        SimpleNamespace(validity_status="CURRENT", outcome="NOT_QUALIFIED", hard_blockers=[]),
        SimpleNamespace(validity_status="CURRENT", outcome="QUALIFIED", hard_blockers=["visa"]),
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
