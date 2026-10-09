import pytest
from types import SimpleNamespace

from app.domain.candidate.errors import InvalidEvidenceTransition
from app.domain.candidate.service import transition_evidence
from app.domain.candidate.states import can_transition


@pytest.mark.parametrize(
    ("current", "allowed_targets"),
    [
        ("DRAFT", {"PENDING_REVIEW", "ARCHIVED"}),
        ("PENDING_REVIEW", {"APPROVED", "REJECTED", "ARCHIVED"}),
        ("APPROVED", {"ARCHIVED"}),
        ("REJECTED", {"ARCHIVED", "PENDING_REVIEW"}),
        ("ARCHIVED", set()),
    ],
)
def test_evidence_state_graph_matches_declared_transition_table(current, allowed_targets):
    all_states = {"DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED", "ARCHIVED"}
    for target in all_states:
        assert can_transition(current, target) is (target in allowed_targets)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("DRAFT", "APPROVED"),
        ("DRAFT", "REJECTED"),
        ("PENDING_REVIEW", "DRAFT"),
        ("APPROVED", "PENDING_REVIEW"),
        ("ARCHIVED", "DRAFT"),
        ("ARCHIVED", "PENDING_REVIEW"),
        ("ARCHIVED", "APPROVED"),
    ],
)
def test_forbidden_evidence_transitions_fail_without_mutation(current, target):
    evidence = SimpleNamespace(
        status=current,
        revision=4,
        verified_at=None,
        updated_at=None,
    )
    with pytest.raises(InvalidEvidenceTransition):
        transition_evidence(evidence, target)
    assert evidence.status == current
    assert evidence.revision == 4
