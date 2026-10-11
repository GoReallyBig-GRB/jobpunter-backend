from app.domain.jobs.completeness import JobDescriptionStatus, assess_job_completeness
from app.application.jobs.normalization import normalize_raw_record


def test_complete_source_record_is_evaluable():
    normalized = normalize_raw_record({
        "job_title": "Analyst",
        "company": "Example Co",
        "description": (
            "Responsibilities include coordinating work, managing delivery, supporting stakeholders, "
            "developing plans, reviewing outcomes, improving processes, reporting progress, and resolving "
            "issues. Requirements include relevant experience, strong communication skills, analytical "
            "ability, knowledge of delivery methods, and experience working across teams. "
        ) * 4,
        "apply_url": "https://example.test/apply",
    })

    result = assess_job_completeness(normalized)

    assert result.status == JobDescriptionStatus.FULL
    assert result.suitable_for_evaluation is True
    assert result.missing_fields == ()


def test_title_and_employer_alone_are_not_complete():
    normalized = normalize_raw_record({"title": "Analyst", "company": "Example Co"})

    result = assess_job_completeness(normalized)

    assert result.status == JobDescriptionStatus.PARTIAL
    assert result.suitable_for_evaluation is False
    assert "job_description" in result.missing_fields
    assert "application_url" in result.missing_fields


def test_missing_title_or_employer_requires_review():
    result = assess_job_completeness({
        "canonical_title": "Analyst",
        "company_name": None,
        "job_description": "Some description",
        "application_url": "https://example.test/apply",
    })

    assert result.status == JobDescriptionStatus.REQUIRES_REVIEW
    assert result.suitable_for_evaluation is False


def test_explicit_closed_stale_and_inaccessible_states_take_precedence():
    complete = {
        "canonical_title": "Analyst",
        "company_name": "Example Co",
        "job_description": "Description",
        "application_url": "https://example.test/apply",
    }

    assert assess_job_completeness(complete, lifecycle_status="CLOSED").status == JobDescriptionStatus.CLOSED
    assert assess_job_completeness(complete, freshness_status="STALE").status == JobDescriptionStatus.STALE
    assert assess_job_completeness(complete, retrieval_status="URL_INACCESSIBLE").status == JobDescriptionStatus.URL_INACCESSIBLE


def test_normalization_does_not_invent_missing_salary_or_location():
    normalized = normalize_raw_record({"title": "Analyst", "company": "Example Co"})

    assert normalized["salary_min"] is None
    assert normalized["salary_max"] is None
    assert normalized["location_country"] is None
    assert normalized["job_description"] is None


def test_long_boilerplate_does_not_count_as_complete_job_description():
    normalized = normalize_raw_record({
        "title": "Analyst",
        "company": "Example Co",
        "description": ("Join our company for an exciting opportunity with a great team and benefits. " * 12),
        "apply_url": "https://example.test/apply",
    })
    result = assess_job_completeness(normalized)
    assert result.status == JobDescriptionStatus.PARTIAL
    assert result.suitable_for_evaluation is False
    assert "job_description" in result.missing_fields
