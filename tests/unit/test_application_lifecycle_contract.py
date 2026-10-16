"""Exhaustive application lifecycle contract tests.

These tests keep the API vocabulary, domain transition matrix, and candidate-control
rules aligned without requiring a database.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import get_args

import pytest

from app.application.opportunities.schemas import ApplicationStatus
from app.domain.opportunities.service import APPLICATION_TRANSITIONS, transition_application


CANONICAL_STATUSES = set(get_args(ApplicationStatus))


def _application(status: str):
    return SimpleNamespace(
        status=status,
        revision=4,
        updated_at=None,
        submitted_at=None,
    )


def test_api_status_contract_matches_domain_transition_matrix():
    assert set(APPLICATION_TRANSITIONS) == CANONICAL_STATUSES
    assert all(target in CANONICAL_STATUSES for targets in APPLICATION_TRANSITIONS.values() for target in targets)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source, targets in APPLICATION_TRANSITIONS.items()
        for target in sorted(targets)
    ],
)
def test_every_declared_application_transition_is_applied_and_auditable(source, target):
    application = _application(source)
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)

    event = transition_application(
        application,
        target,
        actor_type="USER",
        reason="Candidate recorded this lifecycle change",
        notes="Explicit candidate-provided context",
        now=now,
    )

    assert application.status == target
    assert application.revision == 5
    assert application.updated_at == now
    assert application.submitted_at == (now if target == "SUBMITTED" else None)
    assert event == {
        "event_type": "APPLICATION_STATUS_CHANGED",
        "from_status": source,
        "to_status": target,
        "actor_type": "USER",
        "occurred_at": now,
        "reason": "Candidate recorded this lifecycle change",
        "notes": "Explicit candidate-provided context",
    }


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source, targets in APPLICATION_TRANSITIONS.items()
        for target in sorted(CANONICAL_STATUSES - targets)
    ],
)
def test_every_undeclared_application_transition_is_rejected_without_mutation(source, target):
    application = _application(source)
    original = (application.status, application.revision, application.updated_at, application.submitted_at)

    with pytest.raises(ValueError, match="Invalid application transition"):
        transition_application(
            application,
            target,
            actor_type="USER",
            reason="Candidate requested a transition",
        )

    assert (application.status, application.revision, application.updated_at, application.submitted_at) == original


def test_system_cannot_fabricate_a_real_world_application_milestone():
    application = _application("SUBMITTED")
    original = (application.status, application.revision, application.updated_at, application.submitted_at)

    with pytest.raises(ValueError, match="authenticated user action"):
        transition_application(
            application,
            "INTERVIEW",
            actor_type="SYSTEM",
            reason="Unverified automated event",
        )

    assert (application.status, application.revision, application.updated_at, application.submitted_at) == original
