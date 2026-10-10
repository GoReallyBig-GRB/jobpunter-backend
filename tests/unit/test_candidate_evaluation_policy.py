import pytest

from app.application.opportunities.evaluation_policy import evaluate_candidate_job


def test_missing_description_requires_review_not_false_confidence():
    result = evaluate_candidate_job(
        job={"canonical_title": "Technical Product Manager"},
        job_description="",
        description_version_id=None,
        description_completeness="MISSING",
        candidate_evidence=[{"content": {"skills": ["Python"]}}],
        preferences={},
    )
    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert result["explanation"]["confidence"] == "LOW"


def test_explicit_exclusion_overrides_strong_evidence():
    result = evaluate_candidate_job(
        job={"canonical_title": "Senior Sales Manager", "employment_type": "FULL_TIME",
             "metadata": {"industry": "Insurance"}},
        job_description=("This role requires extensive sales experience and managing teams. " * 8),
        description_version_id="jd-version-1",
        description_completeness="COMPLETE",
        candidate_evidence=[{"content": "sales experience managing teams insurance"}],
        preferences={"excludedIndustries": ["insurance"]},
    )
    assert result["outcome"] == "REJECT"
    assert result["score"] == 0
    assert result["hard_blockers"][0]["code"] == "EXCLUDED_INDUSTRY"


def test_same_job_can_produce_candidate_specific_outcomes():
    job = {"canonical_title": "Technical Product Manager", "application_url": "https://example.com/jobs/technical-product-manager", "metadata": {}}
    description = (
        "Required experience with product roadmaps, API integrations, SQL data models, "
        "technical requirements, agile delivery, and engineering teams. " * 4
    )
    shared = dict(job=job, job_description=description, description_version_id="jd1",
                  description_completeness="COMPLETE", preferences={})
    strong = evaluate_candidate_job(**shared, candidate_evidence=[
        {"content": "Technical product manager delivered product roadmaps API integrations SQL data models technical requirements agile delivery engineering teams"}
    ])
    weak = evaluate_candidate_job(**shared, candidate_evidence=[
        {"content": "Retail cashier managed checkout lines and customer service"}
    ])
    assert strong["outcome"] in {"APPLY", "REVIEW"}
    assert strong["score"] > weak["score"]
    assert strong["evidence_summary"]["matched_terms"] != weak["evidence_summary"]["matched_terms"]


def test_missing_evidence_is_not_a_hard_rejection():
    result = evaluate_candidate_job(
        job={"canonical_title": "Product Manager", "metadata": {}},
        job_description=("Experience with stakeholder management and product delivery. " * 5),
        description_version_id="jd1",
        description_completeness="COMPLETE",
        candidate_evidence=[],
        preferences={},
    )
    assert result["outcome"] in {"WATCH", "REVIEW"}
    assert result["hard_blockers"] == []
    assert result["explanation"]["confidence"] == "LOW"



def test_hard_blocker_precedes_incomplete_description_review():
    result = evaluate_candidate_job(
        job={"canonical_title": "Sales Manager", "metadata": {"industry": "Insurance"}},
        job_description="",
        description_version_id=None,
        description_completeness="MISSING",
        candidate_evidence=[],
        preferences={"excludedIndustries": ["insurance"]},
    )
    assert result["outcome"] == "REJECT"
    assert result["hard_blockers"][0]["code"] == "EXCLUDED_INDUSTRY"



def test_remote_only_with_unknown_work_mode_requires_review():
    result = evaluate_candidate_job(
        job={"canonical_title": "Technical Product Manager", "application_url": "https://example.com/apply", "metadata": {}},
        job_description=("Experience with product delivery and stakeholder management. " * 5),
        description_version_id="jd1",
        description_completeness="COMPLETE",
        candidate_evidence=[{"content": "product delivery stakeholder management"}],
        preferences={"remotePreference": "REMOTE_ONLY"},
    )
    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert any("work mode is not recorded" in gap for gap in result["explanation"]["gaps"])


def test_missing_application_route_stays_watch_even_with_strong_evidence():
    result = evaluate_candidate_job(
        job={"canonical_title": "Technical Product Manager", "metadata": {}},
        job_description=("Experience with product delivery and stakeholder management. " * 5),
        description_version_id="jd1",
        description_completeness="COMPLETE",
        candidate_evidence=[{"content": "product delivery stakeholder management"}],
        preferences={},
    )
    assert result["outcome"] == "WATCH"
    assert result["score"] is None
    assert result["evidence_summary"]["dimensions"]["application_accessibility"] == "UNKNOWN"


def test_unrecorded_work_authorization_requires_review_not_rejection():
    result = evaluate_candidate_job(
        job={"canonical_title": "Product Manager", "location_country": "United States", "metadata": {}},
        job_description=(
            "Applicants must be authorized to work in the United States. "
            "Experience with product delivery and stakeholder management. " * 3
        ),
        description_version_id="jd1",
        description_completeness="COMPLETE",
        candidate_evidence=[{"content": "product delivery stakeholder management"}],
        preferences={},
    )
    assert result["outcome"] == "REVIEW"
    assert result["hard_blockers"] == []
    assert result["explanation"]["confidence"] == "LOW"
    assert "authorization" in result["explanation"]["gaps"][0].casefold()



@pytest.mark.parametrize(
    ("job", "preferences", "expected_blocker"),
    [
        pytest.param(
            {"canonical_title": "Technical Product Manager", "employment_type": "CONTRACT", "metadata": {}},
            {"excludedJobTypes": ["CONTRACT"]},
            "EXCLUDED_JOB_TYPE",
            id="explicitly-excluded-employment-type",
        ),
        pytest.param(
            {"canonical_title": "Technical Product Manager", "location_country": "United States", "metadata": {}},
            {"eligibleCountries": ["Nigeria"]},
            "COUNTRY_INELIGIBLE",
            id="outside-candidate-eligible-countries",
        ),
        pytest.param(
            {
                "canonical_title": "Technical Product Manager",
                "location_country": "United States",
                "location_restrictions": {"countries": ["Nigeria"]},
                "metadata": {},
            },
            {},
            "JOB_LOCATION_RESTRICTION",
            id="conflicts-with-job-country-restriction",
        ),
        pytest.param(
            {"canonical_title": "Technical Product Manager", "work_mode": "ONSITE", "metadata": {}},
            {"remotePreference": "REMOTE_ONLY"},
            "REMOTE_ONLY_MISMATCH",
            id="violates-remote-only-preference",
        ),
    ],
)
def test_explicit_hard_constraints_reject_before_description_or_evidence_scoring(
    job, preferences, expected_blocker
):
    result = evaluate_candidate_job(
        job=job,
        job_description="",
        description_version_id=None,
        description_completeness="MISSING",
        candidate_evidence=[{"content": "strong technical product leadership and delivery experience"}],
        preferences=preferences,
    )
    assert result["outcome"] == "REJECT"
    assert result["score"] == 0
    assert expected_blocker in {blocker["code"] for blocker in result["hard_blockers"]}
    assert result["explanation"]["confidence"] == "HIGH"



@pytest.mark.parametrize(
    ("job", "job_description", "preferences", "expected_gap"),
    [
        pytest.param(
            {
                "canonical_title": "Technical Product Manager",
                "application_url": "https://example.com/apply",
                "metadata": {},
            },
            "Experience with product delivery and stakeholder management. " * 5,
            {"eligibleCountries": ["Nigeria"]},
            "job country is missing",
            id="eligible-country-constraint-with-unknown-job-country",
        ),
        pytest.param(
            {
                "canonical_title": "Technical Product Manager",
                "application_url": "https://example.com/apply",
                "location_restrictions": {"countries": ["Nigeria"]},
                "metadata": {},
            },
            "Experience with product delivery and stakeholder management. " * 5,
            {},
            "job country is missing",
            id="job-country-restriction-with-unknown-job-country",
        ),
        pytest.param(
            {
                "canonical_title": "Technical Product Manager",
                "application_url": "https://example.com/apply",
                "metadata": {},
            },
            "Applicants must be authorized to work in the United States. " +
            "Experience with product delivery and stakeholder management. " * 4,
            {"workAuthorizationCountries": ["Nigeria"]},
            "job country is not recorded",
            id="work-authorization-requirement-with-unknown-job-country",
        ),
    ],
)
def test_unknown_job_country_requires_review_when_eligibility_constraints_apply(
    job, job_description, preferences, expected_gap
):
    result = evaluate_candidate_job(
        job=job,
        job_description=job_description,
        description_version_id="jd-country-unknown",
        description_completeness="COMPLETE",
        candidate_evidence=[{"content": "product delivery stakeholder management"}],
        preferences=preferences,
    )
    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert result["hard_blockers"] == []
    assert result["explanation"]["confidence"] == "LOW"
    assert any(expected_gap in gap.casefold() for gap in result["explanation"]["gaps"])
