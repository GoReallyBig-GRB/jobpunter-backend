from app.application.jobs.normalization import candidate_excludes_sales


def test_candidate_sales_exclusion_matches_explicit_preferences():
    assert candidate_excludes_sales({"excludedJobTypes": ["Quota-carrying SDR"]}) is True
    assert candidate_excludes_sales({"excludedIndustries": ["Sales"]}) is True
    assert candidate_excludes_sales({"excludedJobTypes": ["Technical Product Manager"]}) is False
    assert candidate_excludes_sales({}) is False
