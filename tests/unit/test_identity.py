import pytest

from app.application.jobs.identity import (
    IdentityCandidate,
    ResolutionStatus,
    decide_identity,
    validate_identity_candidates,
)


def test_identity_candidates_accept_valid_confidence():
    validate_identity_candidates([IdentityCandidate("job-1", 0.95, "deterministic")])


def test_duplicate_candidate_ids_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        validate_identity_candidates([
            IdentityCandidate("job-1", 0.95, "exact"),
            IdentityCandidate("job-1", 0.90, "fingerprint"),
        ])


def test_weak_match_creates_new_job_instead_of_merging():
    result = decide_identity([IdentityCandidate("job-1", 0.92, "fingerprint")])
    assert result.status == ResolutionStatus.NEW
    assert result.job_id is None


def test_high_confidence_match_resolves():
    result = decide_identity([IdentityCandidate("job-1", 1.0, "source_external_id")])
    assert result.status == ResolutionStatus.MATCHED
    assert result.job_id == "job-1"


def test_close_high_confidence_candidates_require_review():
    result = decide_identity([
        IdentityCandidate("job-1", 0.99, "url"),
        IdentityCandidate("job-2", 0.98, "fingerprint"),
    ])
    assert result.status == ResolutionStatus.REQUIRES_REVIEW
    assert result.job_id is None
    assert result.candidate_job_ids == ("job-1", "job-2")
