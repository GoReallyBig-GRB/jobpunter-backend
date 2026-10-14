import pytest
from pydantic import ValidationError

from app.application.preferences.schemas import CandidatePreferencesData


def test_positive_maximum_application_fee_requires_currency():
    with pytest.raises(ValidationError, match="maxApplicationFeeCurrency is required"):
        CandidatePreferencesData.model_validate({"maxApplicationFee": 100})


def test_positive_maximum_application_fee_accepts_explicit_currency():
    preferences = CandidatePreferencesData.model_validate({
        "maxApplicationFee": 100,
        "maxApplicationFeeCurrency": "USD",
    })

    assert preferences.max_application_fee == 100
    assert preferences.max_application_fee_currency == "USD"


def test_zero_maximum_application_fee_does_not_require_currency():
    preferences = CandidatePreferencesData.model_validate({"maxApplicationFee": 0})

    assert preferences.max_application_fee == 0
    assert preferences.max_application_fee_currency is None


def test_evidence_exclusions_accept_only_unique_exact_evidence_uuids():
    from uuid import uuid4

    evidence_id = str(uuid4())
    preferences = CandidatePreferencesData.model_validate({"evidenceExclusions": [evidence_id.upper()]})
    assert preferences.evidence_exclusions == [evidence_id]


@pytest.mark.parametrize("selectors", [["project"], ["evidence_type:PROJECT"], ["not-a-uuid"]])
def test_evidence_exclusions_reject_ambiguous_non_id_selectors(selectors):
    with pytest.raises(ValidationError, match="exact Evidence Library UUIDs"):
        CandidatePreferencesData.model_validate({"evidenceExclusions": selectors})


def test_evidence_exclusions_reject_duplicate_ids():
    from uuid import uuid4

    evidence_id = str(uuid4())
    with pytest.raises(ValidationError, match="duplicate evidence IDs"):
        CandidatePreferencesData.model_validate({"evidenceExclusions": [evidence_id, evidence_id]})
