from datetime import datetime, timedelta, timezone

import pytest

from app.domain.opportunities.evaluation import evaluate_candidate


NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
REQUIREMENTS = [
    {"id": "r1", "description": "Has product experience", "hard_constraint": False,
     "importance": "HIGH", "extraction_status": "COMPLETE"},
    {"id": "r2", "description": "Eligible to work in location", "hard_constraint": True,
     "importance": "CRITICAL", "extraction_status": "COMPLETE"},
]
EVIDENCE = [
    {"id": "e1", "status": "APPROVED", "expires_at": None},
    {"id": "e2", "status": "APPROVED", "expires_at": NOW + timedelta(days=10)},
]


def test_qualified_requires_approved_evidence_and_threshold():
    result = evaluate_candidate(
        REQUIREMENTS,
        [
            {"requirement_id": "r1", "result": "MATCH", "evidence_ids": ["e1"], "rationale": "Supported"},
            {"requirement_id": "r2", "result": "MATCH", "evidence_ids": ["e2"], "rationale": "Supported"},
        ],
        EVIDENCE,
        now=NOW,
    )
    assert result.outcome == "QUALIFIED"
    assert result.score == 100.0
    assert result.hard_blockers == []
    assert result.policy_version == "deterministic-evaluation-v1"


def test_explicit_hard_constraint_miss_disqualifies():
    result = evaluate_candidate(
        REQUIREMENTS,
        [
            {"requirement_id": "r1", "result": "MATCH", "evidence_ids": ["e1"]},
            {"requirement_id": "r2", "result": "NO_MATCH", "evidence_ids": []},
        ],
        EVIDENCE,
        now=NOW,
    )
    assert result.outcome == "NOT_QUALIFIED"
    assert result.hard_blockers[0]["requirement_id"] == "r2"


def test_unknown_or_unresolved_requirement_requires_review():
    result = evaluate_candidate(
        REQUIREMENTS,
        [{"requirement_id": "r1", "result": "MATCH", "evidence_ids": ["e1"]}],
        EVIDENCE,
        now=NOW,
    )
    assert result.outcome == "NEEDS_REVIEW"
    assert result.evidence_summary["requirements_unresolved"] == 1


@pytest.mark.parametrize("status,expires_at", [
    ("PENDING_REVIEW", None),
    ("APPROVED", NOW - timedelta(seconds=1)),
])
def test_unapproved_or_expired_evidence_cannot_support_match(status, expires_at):
    result = evaluate_candidate(
        [REQUIREMENTS[0]],
        [{"requirement_id": "r1", "result": "MATCH", "evidence_ids": ["e1"]}],
        [{"id": "e1", "status": status, "expires_at": expires_at}],
        now=NOW,
    )
    assert result.outcome == "NEEDS_REVIEW"
    assert result.evidence_summary["rejected_evidence_ids"] == ["e1"]


def test_invalid_assessment_reference_is_rejected():
    with pytest.raises(ValueError, match="unknown requirement"):
        evaluate_candidate(
            REQUIREMENTS,
            [{"requirement_id": "not-a-requirement", "result": "MATCH", "evidence_ids": ["e1"]}],
            EVIDENCE,
            now=NOW,
        )


def test_match_without_evidence_is_not_treated_as_supported():
    result = evaluate_candidate(
        [REQUIREMENTS[0]],
        [{"requirement_id": "r1", "result": "MATCH", "evidence_ids": []}],
        EVIDENCE,
        now=NOW,
    )
    assert result.outcome == "NEEDS_REVIEW"
    assert result.score == 0.0
