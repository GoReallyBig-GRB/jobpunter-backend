from app.application.jobs.normalization import detect_duplicates


def test_duplicate_detection_uses_external_id_then_canonical_url_then_company_title():
    existing = {
        "id": "old",
        "title": "Technical Product Manager",
        "company": "Acme Inc",
        "jobUrl": "https://jobs.example.com/roles/1?utm_source=email",
        "externalJobId": "greenhouse_123",
    }
    by_external_id = detect_duplicates(
        {"id": "new", "title": "Different title", "company": "Other", "externalJobId": "greenhouse_123"},
        [existing],
    )
    assert by_external_id["isDuplicate"] is True
    assert "external job posting ID" in by_external_id["reason"]

    by_url = detect_duplicates(
        {"id": "new", "title": "Different title", "company": "Other", "jobUrl": "https://jobs.example.com/roles/1?ref=job"},
        [existing],
    )
    assert by_url["isDuplicate"] is True
    assert "Exact job URL" in by_url["reason"]

    by_company_title = detect_duplicates(
        {"id": "new", "title": "Technical Product Manager", "company": "Acme Inc"},
        [existing],
    )
    assert by_company_title["isDuplicate"] is True
    assert "Same employer" in by_company_title["reason"]


def test_duplicate_detection_ignores_same_record_and_different_jobs():
    existing = {"id": "same", "title": "Product Manager", "company": "Acme", "jobUrl": "https://example.com/1"}
    assert detect_duplicates(existing, [existing]) == {"isDuplicate": False}
    assert detect_duplicates(
        {"id": "new", "title": "Designer", "company": "Different Co", "jobUrl": "https://example.com/2"},
        [existing],
    ) == {"isDuplicate": False}
