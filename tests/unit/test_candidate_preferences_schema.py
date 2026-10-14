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
