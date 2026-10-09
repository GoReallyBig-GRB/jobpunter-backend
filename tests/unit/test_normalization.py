from app.application.jobs.normalization import (
    assess_description_completeness,
    normalize_raw_record,
    normalize_url,
)


def test_normalization_maps_common_source_fields():
    result = normalize_raw_record({
        "job_title": "  Product   Manager ",
        "company": "Acme",
        "url": "https://EXAMPLE.com/job/?utm_source=board&id=123#apply",
    })
    assert result["canonical_title"] == "Product Manager"
    assert result["company_name"] == "Acme"
    assert result["application_url"] == "https://example.com/job?id=123"
    assert result["identity_fingerprint"]


def test_url_normalization_preserves_job_identifiers_and_removes_tracking():
    assert normalize_url("https://example.com/jobs/1?gh_jid=abc&utm_campaign=x") == (
        "https://example.com/jobs/1?gh_jid=abc"
    )


def test_short_descriptions_are_incomplete():
    assert assess_description_completeness("A short description") == "INCOMPLETE"


def test_normalization_maps_description_and_employer_aliases():
    result = normalize_raw_record({
        "position": "Analyst",
        "employer": "Example Ltd",
        "job_description": "x " * 200,
    })
    assert result["canonical_title"] == "Analyst"
    assert result["company_name"] == "Example Ltd"
    assert result["description_completeness"] == "USABLE"
    assert result["description_hash"]
