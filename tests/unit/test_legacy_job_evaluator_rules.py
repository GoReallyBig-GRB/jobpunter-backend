from app.application.jobs.normalization import (
    determine_jd_retrieval_status,
    evaluate_parser_confidence,
)


def test_parser_confidence_matches_legacy_required_field_rules():
    assert evaluate_parser_confidence({
        "title": "Product Manager",
        "company": "Acme",
        "jobUrl": "https://example.com/jobs/1",
        "description": "x" * 80,
    }) == {"confidence": "HIGH", "missingFields": []}

    result = evaluate_parser_confidence({
        "title": "Product Manager",
        "company": "Acme",
        "description": "x" * 80,
    })
    assert result == {
        "confidence": "MEDIUM",
        "missingFields": ["Job / Application URL"],
    }

    result = evaluate_parser_confidence({"description": "short"})
    assert result["confidence"] == "LOW"
    assert "Job Title" in result["missingFields"]
    assert "Company Name" in result["missingFields"]
    assert "Meaningful Job Description (content too brief to assess)" in result["missingFields"]


def test_jd_retrieval_status_matches_legacy_precedence_and_thresholds():
    assert determine_jd_retrieval_status({
        "jdRetrievalStatus": "CUSTOM",
        "description": "closed",
    }) == "CUSTOM"
    assert determine_jd_retrieval_status({
        "description": "This role is closed now",
        "jobUrl": "https://example.com/job",
    }) == "CLOSED"
    assert determine_jd_retrieval_status({"description": "x" * 301, "jobUrl": "https://example.com"}) == (
        "FULL_JD_RETRIEVED"
    )
    assert determine_jd_retrieval_status({"description": "x" * 81, "jobUrl": "https://example.com"}) == (
        "PARTIAL_JD_RETRIEVED"
    )
    assert determine_jd_retrieval_status({"description": "short", "jobUrl": "https://example.com"}) == (
        "JD_UNAVAILABLE"
    )
    assert determine_jd_retrieval_status({"description": "x" * 500}) == "REQUIRES_REVIEW"
