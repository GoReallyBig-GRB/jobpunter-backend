from app.application.jobs.normalization import normalize_raw_record

def test_normalization_maps_common_source_fields():
    result = normalize_raw_record({"job_title":"Product Manager","company":"Acme","url":"https://example.com/job"})
    assert result["canonical_title"] == "Product Manager"
    assert result["company_name"] == "Acme"
    assert result["application_url"] == "https://example.com/job"
