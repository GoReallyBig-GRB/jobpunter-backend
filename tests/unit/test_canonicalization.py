from app.application.jobs.identity import IdentityCandidate, ResolutionStatus, decide_identity


def test_exact_identity_candidates_are_matched():
    result = decide_identity([IdentityCandidate("job-1", 1.0, "EXACT_APPLICATION_URL")])
    assert result.status == ResolutionStatus.MATCHED
    assert result.job_id == "job-1"


def test_multiple_exact_candidates_require_review_instead_of_merge():
    result = decide_identity([
        IdentityCandidate("job-1", 1.0, "EXACT_APPLICATION_URL"),
        IdentityCandidate("job-2", 1.0, "EXACT_NORMALIZED_FINGERPRINT"),
    ])
    assert result.status == ResolutionStatus.REQUIRES_REVIEW
    assert result.job_id is None
    assert set(result.candidate_job_ids) == {"job-1", "job-2"}
