from app.application.jobs.normalization import normalize_raw_record


def test_normalization_preserves_explicit_typed_fee_and_relocation_facts():
    normalized = normalize_raw_record({
        "title": "Product Manager",
        "application_fee_amount": 25,
        "application_fee_currency": "USD",
        "relocation_required": True,
    })

    assert normalized["application_fee_amount"] == 25.0
    assert normalized["application_fee_currency"] == "USD"
    assert normalized["relocation_required"] is True


def test_normalization_does_not_infer_fee_or_relocation_facts():
    normalized = normalize_raw_record({
        "title": "Product Manager",
        "location": "London, UK",
        "description": "Relocation may be possible.",
    })

    assert normalized["application_fee_amount"] is None
    assert normalized["application_fee_currency"] is None
    assert normalized["relocation_required"] is None


def test_normalization_rejects_stringly_typed_relocation_flag():
    normalized = normalize_raw_record({
        "title": "Product Manager",
        "relocation_required": "yes",
    })

    assert normalized["relocation_required"] is None
