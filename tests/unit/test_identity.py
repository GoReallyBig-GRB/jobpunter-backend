import pytest
from app.application.jobs.identity import IdentityCandidate, validate_identity_candidates

def test_identity_candidates_accept_valid_confidence():
    validate_identity_candidates([IdentityCandidate("job-1", 0.95, "deterministic")])

def test_identity_candidates_reject_ties():
    with pytest.raises(ValueError):
        validate_identity_candidates([
            IdentityCandidate("job-1", 0.9, "heuristic"),
            IdentityCandidate("job-2", 0.9, "heuristic"),
        ])
