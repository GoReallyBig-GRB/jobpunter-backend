import pytest

from app.application.opportunities.evaluation_policy import evaluate_candidate_job, validate_decision_rules



def test_published_soft_thresholds_change_scoring_without_changing_hard_constraints():
    job = {
        "canonical_title": "Operations Manager",
        "application_url": "https://example.test/apply",
        "lifecycle_status": "ACTIVE",
        "freshness_status": "FRESH",
        "application_route_verification": {
            "status": "ACTIVE",
            "checked_url": "https://example.test/apply",
        },
        "metadata": {},
    }
    shared = {
        "job": job,
        "job_description": (
            "Responsibilities include managing delivery and coordinating teams. "
            "Requirements include relevant experience and analytical skills. "
        ) * 5,
        "description_version_id": "jd-thresholds",
        "description_completeness": "COMPLETE",
        "candidate_evidence": [{"id": "ev-1", "content": "Approved evidence supports delivery."}],
        "preferences": {},
        "semantic_matches": [
            {
                "requirement_id": f"REQ-{index}",
                "requirement_text": f"Requirement {index}",
                "status": "DIRECT" if index <= 4 else "MISSING",
                "rationale": "Grounded test match.",
                "evidence_refs": ["ev-1"] if index <= 4 else [],
                "mandatory": index < 4,
            }
            for index in range(1, 6)
        ],
    }

    default_result = evaluate_candidate_job(**shared)
    governed_result = evaluate_candidate_job(
        **shared,
        decision_rules={"apply_min_score": 90, "review_min_score": 35},
        policy_version_override="candidate-evaluation-v20+rules-v1",
    )

    assert default_result["outcome"] == "APPLY"
    assert governed_result["outcome"] == "REVIEW"
    assert governed_result["score"] == default_result["score"]
    assert governed_result["policy_version"] == "candidate-evaluation-v20+rules-v1"


@pytest.mark.parametrize(
    "rules",
    [
        {"apply_min_score": 35, "review_min_score": 35},
        {"apply_min_score": 101, "review_min_score": 35},
        {"apply_min_score": True, "review_min_score": 35},
        {"apply_min_score": 80, "review_min_score": 35, "excluded_industries": []},
    ],
)
def test_decision_rule_validation_rejects_invalid_thresholds_and_hard_rule_overrides(rules):
    with pytest.raises(ValueError):
        validate_decision_rules(rules)



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
        candidate_evidence=[{
            "id": "e1",
            "content": "sales experience managing teams insurance",
        }],
        preferences={"excludedIndustries": ["insurance"]},
        semantic_matches=[{
            "requirement_id": "REQ-1",
            "requirement_text": "Extensive sales experience and team management",
            "status": "DIRECT",
            "rationale": "Approved evidence directly supports the role requirement.",
            "evidence_refs": ["e1"],
            "mandatory": True,
        }],
    )
    assert result["outcome"] == "REJECT"
    assert result["score"] == 0
    assert result["hard_blockers"][0]["code"] == "EXCLUDED_INDUSTRY"


def test_keyword_overlap_alone_does_not_establish_candidate_fit():
    job = {"canonical_title": "Technical Product Manager", "application_url": "https://example.com/jobs/technical-product-manager", "metadata": {}}
    description = (
        "Required experience with product roadmaps, API integrations, SQL data models, "
        "technical requirements, agile delivery, and engineering teams. " * 4
    )
    shared = dict(job=job, job_description=description, description_version_id="jd1",
                  description_completeness="COMPLETE", preferences={})
    strong_overlap = evaluate_candidate_job(**shared, candidate_evidence=[
        {"id": "e1", "content": "Technical product manager delivered product roadmaps API integrations SQL data models technical requirements agile delivery engineering teams"}
    ])
    weak_overlap = evaluate_candidate_job(**shared, candidate_evidence=[
        {"id": "e2", "content": "Retail cashier managed checkout lines and customer service"}
    ])
    assert strong_overlap["outcome"] == "REVIEW"
    assert strong_overlap["score"] is None
    assert weak_overlap["outcome"] == "REVIEW"
    assert weak_overlap["score"] is None


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


@pytest.mark.parametrize(
    ("job_type", "preferences", "expected_outcome", "expected_code"),
    [
        pytest.param(
            "CONTRACT",
            {"employmentTypes": ["FULL_TIME"]},
            "REJECT",
            "EMPLOYMENT_TYPE_MISMATCH",
            id="job-type-outside-candidate-allowed-types",
        ),
        pytest.param(
            None,
            {"employmentTypes": ["FULL_TIME"]},
            "REVIEW",
            None,
            id="candidate-employment-type-constraint-with-unknown-job-type",
        ),
    ],
)
def test_allowed_employment_type_preference_is_enforced_deterministically(
    job_type, preferences, expected_outcome, expected_code
):
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "employment_type": job_type,
            "application_url": "https://example.com/apply",
            "metadata": {},
        },
        job_description="Experience with product delivery and stakeholder management. " * 5,
        description_version_id="jd-employment-type",
        description_completeness="COMPLETE",
        candidate_evidence=[{"content": "product delivery and stakeholder management"}],
        preferences=preferences,
    )

    assert result["outcome"] == expected_outcome
    if expected_code:
        assert expected_code in {item["code"] for item in result["hard_blockers"]}
    else:
        assert result["hard_blockers"] == []
        assert any(
            "employment type is missing" in gap.casefold()
            for gap in result["explanation"]["gaps"]
        )


def test_missing_application_route_stays_watch_even_with_strong_evidence():
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "ACTIVE",
            "freshness_status": "FRESH",
            "metadata": {},
        },
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



def _evaluate_salary_case(*, job_salary_min=70000, job_salary_max=90000,
                          job_currency="USD", job_period="YEAR", candidate_min=80000,
                          candidate_currency="USD", candidate_period="YEAR"):
    return evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "application_url": "https://example.com/apply",
            "salary_min": job_salary_min,
            "salary_max": job_salary_max,
            "salary_currency": job_currency,
            "salary_period": job_period,
            "metadata": {},
        },
        job_description="Experience with product delivery and stakeholder management. " * 5,
        description_version_id="jd-salary",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "e1", "content": "product delivery and stakeholder management"}],
        preferences={
            "salaryMin": candidate_min,
            "salaryCurrency": candidate_currency,
            "salaryPeriod": candidate_period,
        },
    )


def test_salary_below_candidate_minimum_is_a_hard_rejection_when_units_match():
    result = _evaluate_salary_case(job_salary_min=70000, job_salary_max=70000)

    assert result["outcome"] == "REJECT"
    assert result["score"] == 0
    assert any(item["code"] == "SALARY_BELOW_MINIMUM" for item in result["hard_blockers"])


def test_salary_at_candidate_minimum_passes_salary_gate():
    result = _evaluate_salary_case(job_salary_min=80000, job_salary_max=80000)

    # The policy still requires validated semantic matching before recommending APPLY.
    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert "SALARY_BELOW_MINIMUM" not in {
        item["code"] for item in result["hard_blockers"]
    }
    assert not any("salary" in gap.casefold() for gap in result["explanation"]["gaps"])


def test_undisclosed_job_salary_requires_review_not_qualification():
    result = _evaluate_salary_case(job_salary_min=None, job_salary_max=None)

    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert result["hard_blockers"] == []
    assert "maximum salary is not disclosed" in result["explanation"]["gaps"][0]


@pytest.mark.parametrize(
    ("job_currency", "job_period", "candidate_currency", "candidate_period"),
    [
        ("EUR", "YEAR", "USD", "YEAR"),
        ("USD", "MONTH", "USD", "YEAR"),
        ("USD", "UNSPECIFIED", "USD", "YEAR"),
    ],
)
def test_incompatible_salary_currency_or_period_requires_review(
    job_currency, job_period, candidate_currency, candidate_period
):
    result = _evaluate_salary_case(
        job_salary_min=90000,
        job_currency=job_currency,
        job_period=job_period,
        candidate_currency=candidate_currency,
        candidate_period=candidate_period,
    )

    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert result["hard_blockers"] == []
    assert "cannot be compared" in result["explanation"]["gaps"][0]


def test_salary_range_overlapping_candidate_minimum_is_not_rejected():
    result = _evaluate_salary_case(job_salary_min=70000, job_salary_max=100000)

    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert "SALARY_BELOW_MINIMUM" not in {
        item["code"] for item in result["hard_blockers"]
    }
    assert not any("salary" in gap.casefold() for gap in result["explanation"]["gaps"])



@pytest.mark.parametrize(
    ("job_metadata", "preferences", "expected_outcome", "expected_code", "expected_gap"),
    [
        pytest.param(
            {"application_fee_amount": 50, "application_fee_currency": "USD"},
            {"maxApplicationFee": 20, "maxApplicationFeeCurrency": "USD"},
            "REJECT", "APPLICATION_FEE_EXCEEDS_MAXIMUM", None,
            id="fee-exceeds-candidate-limit",
        ),
        pytest.param(
            {"application_fee_amount": 50, "application_fee_currency": "USD"},
            {"maxApplicationFee": 20, "maxApplicationFeeCurrency": "EUR"},
            "REVIEW", None, "currencies differ",
            id="fee-currency-mismatch-needs-review",
        ),
        pytest.param(
            {},
            {"maxApplicationFee": 0},
            "REVIEW", None, "fee is unknown",
            id="unknown-fee-with-explicit-zero-limit",
        ),
    ],
)
def test_application_fee_constraint_uses_only_comparable_explicit_facts(
    job_metadata, preferences, expected_outcome, expected_code, expected_gap
):
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "application_url": "https://example.com/apply",
            "metadata": job_metadata,
        },
        job_description="Experience with product delivery and stakeholder management. " * 5,
        description_version_id="jd-fee-eligibility",
        description_completeness="COMPLETE",
        candidate_evidence=[{"content": "product delivery and stakeholder management"}],
        preferences=preferences,
    )

    assert result["outcome"] == expected_outcome
    assert result["policy_version"] == "candidate-evaluation-v20"
    if expected_code:
        assert expected_code in {item["code"] for item in result["hard_blockers"]}
    if expected_gap:
        assert any(expected_gap in gap.casefold() for gap in result["explanation"]["gaps"])


@pytest.mark.parametrize(
    ("relocation_required", "preferences", "expected_outcome", "expected_code", "expected_gap"),
    [
        pytest.param(
            True, {"willingToRelocate": False}, "REJECT", "RELOCATION_NOT_ACCEPTED", None,
            id="candidate-declines-required-relocation",
        ),
        pytest.param(
            True, {"willingToRelocate": None}, "REVIEW", None, "relocation preference is unknown",
            id="required-relocation-with-unknown-preference",
        ),
        pytest.param(
            False, {"willingToRelocate": False}, "REVIEW", None, None,
            id="no-relocation-required-does-not-reject",
        ),
    ],
)
def test_relocation_constraint_requires_explicit_job_fact(
    relocation_required, preferences, expected_outcome, expected_code, expected_gap
):
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "application_url": "https://example.com/apply",
            "metadata": {"relocation_required": relocation_required},
        },
        job_description="Experience with product delivery and stakeholder management. " * 5,
        description_version_id="jd-relocation-eligibility",
        description_completeness="COMPLETE",
        candidate_evidence=[{"content": "product delivery and stakeholder management"}],
        preferences=preferences,
    )

    assert result["outcome"] == expected_outcome
    if expected_code:
        assert expected_code in {item["code"] for item in result["hard_blockers"]}
    if expected_gap:
        assert any(expected_gap in gap.casefold() for gap in result["explanation"]["gaps"])



@pytest.mark.parametrize(
    ("freshness_status", "expected_outcome", "expected_dimension"),
    [
        ("STALE", "WATCH", "STALE"),
        ("UNKNOWN", "REVIEW", "UNKNOWN"),
        ("FRESH", "APPLY", "CURRENT"),
    ],
)
def test_job_freshness_is_independent_of_semantic_fit(
    freshness_status, expected_outcome, expected_dimension
):
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "ACTIVE",
            "freshness_status": freshness_status,
            "application_url": "https://example.test/apply",
            "application_route_verification": {"status": "ACTIVE", "checked_url": "https://example.test/apply"},
            "metadata": {},
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-freshness",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Product delivery and team coordination."}],
        preferences={},
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage delivery",
            "status": "direct",
            "rationale": "Evidence supports the requirement.",
            "evidence_refs": ["ev-1"],
            "mandatory": True,
        }],
    )
    assert result["outcome"] == expected_outcome
    assert result["score"] is None if expected_outcome != "APPLY" else result["score"] == 100
    assert result["evidence_summary"]["dimensions"]["job_freshness"] == expected_dimension


def test_closed_job_watch_precedes_unknown_salary_review():
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "CLOSED",
            "freshness_status": "UNKNOWN",
            "application_url": "https://example.test/apply",
            "metadata": {},
        },
        job_description="",
        description_version_id=None,
        description_completeness="MISSING",
        candidate_evidence=[],
        preferences={"salaryMin": 90000, "salaryCurrency": "USD", "salaryPeriod": "YEAR"},
    )
    assert result["outcome"] == "WATCH"
    assert result["score"] is None
    assert result["evidence_summary"]["dimensions"]["application_accessibility"] == "UNAVAILABLE"



@pytest.mark.parametrize(
    ("match_status", "expected_outcome", "expected_canonical_status"),
    [
        ("DIRECT", "APPLY", "DIRECT"),
        ("STRONG_TRANSFERABLE", "APPLY", "STRONG_TRANSFERABLE"),
        ("TRANSFERABLE", "APPLY", "TRANSFERABLE"),
        ("PARTIAL", "REVIEW", "PARTIAL"),
        ("WEAK_TRANSFERABLE", "REVIEW", "WEAK_TRANSFERABLE"),
        ("MISSING", "REVIEW", "MISSING"),
        ("CONTRADICTED", "REVIEW", "CONTRADICTED"),
        ("REQUIRES_VERIFICATION", "REVIEW", "REQUIRES_VERIFICATION"),
        ("DISQUALIFYING", "REVIEW", "DISQUALIFYING"),
        ("unverified", "REVIEW", "REQUIRES_VERIFICATION"),
    ],
)
def test_prd_evidence_match_states_are_canonical_and_explainable(
    match_status, expected_outcome, expected_canonical_status
):
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "ACTIVE",
            "freshness_status": "FRESH",
            "application_url": "https://example.test/apply",
            "application_route_verification": {"status": "ACTIVE", "checked_url": "https://example.test/apply"},
            "metadata": {},
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-match-states",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved product delivery evidence."}],
        preferences={},
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage product delivery",
            "status": match_status,
            "rationale": "State assessed against approved evidence.",
            "evidence_refs": ["ev-1"] if expected_canonical_status != "MISSING" else [],
            "mandatory": True,
        }],
    )
    assert result["outcome"] == expected_outcome
    match = result["evidence_summary"]["requirement_matches"][0]
    assert match["status"] == expected_canonical_status
    assert result["evidence_summary"]["requirement_match_state_counts"][expected_canonical_status] == 1
    assert result["evidence_summary"]["dimensions"]["mandatory_requirements"] == (
        "MET" if expected_outcome == "APPLY" else "UNMET"
    )



@pytest.mark.parametrize(
    ("route_status", "checked_url", "expected_outcome"),
    [
        ("ACTIVE", "https://example.test/apply", "APPLY"),
        ("UNKNOWN", "https://example.test/apply", "WATCH"),
        ("ACTIVE", "https://other.test/apply", "WATCH"),
    ],
)
def test_application_route_requires_current_url_matched_verification(
    route_status, checked_url, expected_outcome
):
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "ACTIVE",
            "freshness_status": "FRESH",
            "application_url": "https://example.test/apply",
            "application_route_verification": {
                "status": route_status,
                "checked_url": checked_url,
            },
            "metadata": {},
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-route-verification",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved product delivery evidence."}],
        preferences={},
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage product delivery",
            "status": "DIRECT",
            "rationale": "Supported by approved evidence.",
            "evidence_refs": ["ev-1"],
            "mandatory": True,
        }],
    )
    assert result["outcome"] == expected_outcome
    if expected_outcome == "APPLY":
        assert result["evidence_summary"]["dimensions"]["application_accessibility"] == "VERIFIED_ACTIVE"
    else:
        assert result["score"] is None
        assert any("application-route verifier" in gap.casefold() for gap in result["explanation"]["gaps"])



@pytest.mark.parametrize(
    ("metadata", "expected_classification"),
    [
        ({}, "UNKNOWN"),
        ({"application_route_type": "FREE_ATS"}, "FREE_ATS"),
        ({"application_route_classification": "DIRECT_EMPLOYER"}, "DIRECT_EMPLOYER"),
        ({"application_route_classification": "PAID_OR_BLOCKED"}, "PAID_OR_BLOCKED"),
        ({"application_route_type": "UNKNOWN_UNVERIFIED_LABEL"}, "UNKNOWN"),
    ],
)
def test_application_route_classification_uses_only_explicit_metadata(
    metadata, expected_classification
):
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "ACTIVE",
            "freshness_status": "FRESH",
            "application_url": "https://jobs.example.com/role",
            "application_route_verification": {
                "status": "ACTIVE",
                "checked_url": "https://jobs.example.com/role",
            },
            "metadata": metadata,
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-route-classification",
        description_completeness="COMPLETE",
        candidate_evidence=[],
        preferences={},
    )
    assert result["evidence_summary"]["dimensions"]["application_route_classification"] == expected_classification
    assert result["evidence_summary"]["dimension_assessments"]["application_accessibility"]["classification"] == expected_classification



def test_no_approved_evidence_produces_review_and_unknown_fit_not_zero_score():
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "ACTIVE",
            "freshness_status": "FRESH",
            "application_url": "https://example.test/apply",
            "application_route_verification": {
                "status": "ACTIVE",
                "checked_url": "https://example.test/apply",
            },
            "metadata": {},
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-no-evidence",
        description_completeness="COMPLETE",
        candidate_evidence=[],
        preferences={},
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage product delivery",
            "status": "MISSING",
            "rationale": "No approved candidate evidence was supplied.",
            "evidence_refs": [],
            "mandatory": True,
            "dimension": "core_role_fit",
        }],
    )
    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert result["evidence_summary"]["dimensions"]["mandatory_requirements"] == "REQUIRES_REVIEW"
    assert result["evidence_summary"]["dimension_assessments"]["core_role_fit"]["status"] == "NO_EVIDENCE"
    assert any("absence of evidence is not proof" in reason.casefold() for reason in result["explanation"]["reasons"])



def test_unknown_job_lifecycle_cannot_be_treated_as_open():
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "UNKNOWN",
            "freshness_status": "FRESH",
            "application_url": "https://example.test/apply",
            "application_route_verification": {
                "status": "ACTIVE",
                "checked_url": "https://example.test/apply",
            },
            "metadata": {},
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-unknown-lifecycle",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved product delivery evidence."}],
        preferences={},
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage product delivery",
            "status": "DIRECT",
            "rationale": "Supported by approved evidence.",
            "evidence_refs": ["ev-1"],
            "mandatory": True,
        }],
    )
    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert result["evidence_summary"]["dimensions"]["job_lifecycle"] == "UNKNOWN"
    assert result["evidence_summary"]["dimensions"]["job_freshness"] == "CURRENT"
    assessments = result["evidence_summary"]["dimension_assessments"]
    assert assessments["job_lifecycle"]["status"] == "UNKNOWN"
    assert assessments["job_freshness"]["status"] == "CURRENT"



@pytest.mark.parametrize(
    ("route_type", "preferences", "job_metadata", "expected_outcome"),
    [
        ("BLOCKED", {}, {"application_route_type": "BLOCKED"}, "WATCH"),
        ("PAID", {}, {"application_route_type": "PAID"}, "REVIEW"),
        (
            "PAID",
            {"maxApplicationFee": 10, "maxApplicationFeeCurrency": "USD"},
            {
                "application_route_type": "PAID",
                "application_fee_amount": 5,
                "application_fee_currency": "USD",
            },
            "APPLY",
        ),
    ],
)
def test_paid_and_blocked_route_decisions_use_explicit_route_and_fee_facts(
    route_type, preferences, job_metadata, expected_outcome
):
    job_metadata = dict(job_metadata)
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "ACTIVE",
            "freshness_status": "FRESH",
            "application_url": "https://example.test/apply",
            "application_route_verification": {
                "status": "ACTIVE",
                "checked_url": "https://example.test/apply",
            },
            "metadata": job_metadata,
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-paid-blocked-route",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved product delivery evidence."}],
        preferences=preferences,
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage product delivery",
            "status": "DIRECT",
            "rationale": "Supported by approved evidence.",
            "evidence_refs": ["ev-1"],
            "mandatory": True,
        }],
    )
    assert result["outcome"] == expected_outcome
    assert result["evidence_summary"]["dimensions"]["application_route_classification"] == route_type
    if route_type == "BLOCKED":
        assert result["evidence_summary"]["dimension_assessments"]["application_accessibility"]["status"] == "UNAVAILABLE"



def test_soft_role_industry_location_and_work_mode_preferences_are_separate_from_blockers():
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "company_name": "Example Employer",
            "lifecycle_status": "ACTIVE",
            "freshness_status": "FRESH",
            "application_url": "https://example.test/apply",
            "application_route_verification": {
                "status": "ACTIVE",
                "checked_url": "https://example.test/apply",
            },
            "location_city": "London",
            "location_country": "UK",
            "work_mode": "ONSITE",
            "metadata": {
                "industry": "Healthcare",
                "application_route_type": "DIRECT_EMPLOYER",
            },
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-soft-preferences",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved product delivery evidence."}],
        preferences={
            "targetRoles": ["Technical Product Manager"],
            "targetIndustries": ["SaaS"],
            "preferredLocations": ["Nigeria"],
            "remotePreference": "REMOTE_PREFERRED",
            "targetDomains": ["Fintech"],
            "targetSeniority": ["Director"],
        },
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage product delivery",
            "status": "DIRECT",
            "rationale": "Supported by approved evidence.",
            "evidence_refs": ["ev-1"],
            "mandatory": True,
            "dimension": "core_role_fit",
        }],
    )
    assert result["outcome"] == "APPLY"
    assert result["hard_blockers"] == []
    preference_fit = result["evidence_summary"]["dimension_assessments"]["preference_fit"]
    assert preference_fit["status"] == "PARTIAL"
    assert preference_fit["role_preference_match"] is True
    assert preference_fit["industry_preference_match"] is False
    assert preference_fit["domain_preference_match"] is False
    assert preference_fit["seniority_preference_match"] is False
    assert preference_fit["location_preference_match"] is False
    assert preference_fit["work_mode_preference_match"] is False



def test_schema_defined_hybrid_ok_preference_is_assessed_without_false_unknown():
    result = evaluate_candidate_job(
        job={
            "canonical_title": "Technical Product Manager",
            "lifecycle_status": "ACTIVE",
            "freshness_status": "FRESH",
            "application_url": "https://example.test/apply",
            "application_route_verification": {
                "status": "ACTIVE",
                "checked_url": "https://example.test/apply",
            },
            "work_mode": "HYBRID",
            "metadata": {},
        },
        job_description=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-hybrid-ok",
        description_completeness="COMPLETE",
        candidate_evidence=[{
            "id": "ev-1", "type": "experience", "source_type": "candidate_upload",
            "source_reference": "upload://evidence-1", "content": "Approved product delivery evidence.",
        }],
        preferences={"remotePreference": "HYBRID_OK"},
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage product delivery",
            "status": "DIRECT",
            "rationale": "Supported by approved evidence.",
            "evidence_refs": ["ev-1"],
            "mandatory": True,
        }],
    )
    assert result["outcome"] == "APPLY"
    assert result["evidence_summary"]["dimension_assessments"]["preference_fit"]["status"] == "MET"
    assert result["evidence_summary"]["dimension_assessments"]["preference_fit"]["work_mode_preference_match"] is True

@pytest.mark.parametrize(
    ("job_role_family", "expected_match", "expected_status"),
    [
        ("Product Management", True, "MET"),
        ("Engineering", False, "PARTIAL"),
        (None, None, "REQUIRES_REVIEW"),
    ],
)
def test_target_role_family_is_a_soft_preference_and_never_a_hard_blocker(
    job_role_family, expected_match, expected_status
):
    metadata = {"application_route_type": "DIRECT_EMPLOYER"}
    if job_role_family is not None:
        metadata["role_family"] = job_role_family
    job = {
        "canonical_title": "Technical Product Manager",
        "company_name": "Example Employer",
        "lifecycle_status": "ACTIVE",
        "freshness_status": "FRESH",
        "application_url": "https://example.test/apply",
        "application_route_verification": {
            "status": "ACTIVE",
            "checked_url": "https://example.test/apply",
        },
        "metadata": metadata,
    }
    result = evaluate_candidate_job(
        job=job,
        job_description=(
            "Responsibilities include product delivery and coordinating teams. "
            "Requirements include relevant experience and stakeholder management. "
        ) * 4,
        description_version_id="jd-role-family-preference",
        description_completeness="COMPLETE",
        candidate_evidence=[{
            "id": "ev-1",
            "source_type": "candidate_upload",
            "source_reference": "upload://ev-1",
            "content": "Approved evidence for product delivery and stakeholder management.",
        }],
        preferences={"targetRoleFamilies": ["Product Management"]},
        semantic_matches=[{
            "requirement_id": "REQ-001",
            "requirement_text": "Manage product delivery",
            "status": "DIRECT",
            "rationale": "Supported by approved evidence.",
            "evidence_refs": ["ev-1"],
            "mandatory": True,
            "dimension": "core_role_fit",
        }],
    )

    preference_fit = result["evidence_summary"]["dimension_assessments"]["preference_fit"]
    assert result["outcome"] == "APPLY"
    assert result["hard_blockers"] == []
    assert preference_fit["status"] == expected_status
    assert preference_fit["role_family_preference_match"] is expected_match




@pytest.mark.parametrize(
    ("critical_status", "expected_education_status"),
    [
        ("MISSING", "UNMET"),
        ("CONTRADICTED", "CONTRADICTED"),
        ("REQUIRES_VERIFICATION", "REQUIRES_VERIFICATION"),
        ("PARTIAL", "PARTIAL"),
    ],
)
def test_unmet_must_have_requirement_cannot_be_hidden_by_preferred_matches(
    critical_status, expected_education_status,
):
    job = {
        "canonical_title": "Operations Manager",
        "application_url": "https://example.test/apply",
        "lifecycle_status": "ACTIVE",
        "freshness_status": "FRESH",
        "application_route_verification": {
            "status": "ACTIVE",
            "checked_url": "https://example.test/apply",
        },
        "metadata": {},
    }
    matches = [
        {
            "requirement_id": f"REQ-{index}",
            "requirement_text": f"Requirement {index}",
            "status": critical_status if index == 1 else "DIRECT",
            "rationale": "Grounded test match.",
            "evidence_refs": [] if index == 1 else ["ev-1"],
            "mandatory": False,
            "severity": "MUST_HAVE" if index == 1 else "PREFERRED",
            "dimension": "EDUCATION" if index == 1 else "TECHNICAL",
        }
        for index in range(1, 11)
    ]
    result = evaluate_candidate_job(
        job=job,
        job_description=(
            "Responsibilities include managing delivery and coordinating teams. "
            "Requirements include relevant experience and analytical skills. "
        ) * 5,
        description_version_id="jd-must-have-gate",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved evidence supports delivery."}],
        preferences={},
        semantic_matches=matches,
    )
    assert result["score"] == 90
    assert result["outcome"] == "REVIEW"
    assert result["evidence_summary"]["unmet_mandatory_requirement_count"] == 1
    dimensions = result["evidence_summary"]["dimension_assessments"]
    assert dimensions["education_fit"]["status"] == expected_education_status
    assert dimensions["technical_fit"]["status"] == "MET"
    assert result["evidence_summary"]["requirement_matches"][0]["severity"] == "MUST_HAVE"


def test_disqualifying_match_cannot_be_hidden_by_high_preferred_score():
    job = {
        "canonical_title": "Operations Manager",
        "application_url": "https://example.test/apply",
        "lifecycle_status": "ACTIVE",
        "freshness_status": "FRESH",
        "application_route_verification": {
            "status": "ACTIVE",
            "checked_url": "https://example.test/apply",
        },
        "metadata": {},
    }
    matches = [
        {
            "requirement_id": f"REQ-{index}",
            "requirement_text": f"Requirement {index}",
            "status": "DISQUALIFYING" if index == 1 else "DIRECT",
            "rationale": "Grounded test match.",
            "evidence_refs": ["ev-1"],
            "mandatory": False,
            "severity": "PREFERRED",
            "dimension": "EDUCATION" if index == 1 else "TECHNICAL",
        }
        for index in range(1, 11)
    ]
    result = evaluate_candidate_job(
        job=job,
        job_description=(
            "Responsibilities include managing delivery and coordinating teams. "
            "Requirements include relevant experience and analytical skills. "
        ) * 5,
        description_version_id="jd-disqualifying-gate",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved evidence supports delivery."}],
        preferences={},
        semantic_matches=matches,
    )
    assert result["score"] == 90
    assert result["outcome"] == "REVIEW"
    assert result["evidence_summary"]["disqualifying_requirement_count"] == 1
    dimensions = result["evidence_summary"]["dimension_assessments"]
    assert dimensions["education_fit"]["status"] == "DISQUALIFYING"
    assert dimensions["technical_fit"]["status"] == "MET"
    assert any("DISQUALIFYING" in reason for reason in result["explanation"]["reasons"])
