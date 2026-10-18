import pytest

from app.application.opportunities.evaluation_policy import evaluate_candidate_job


def test_semantic_evidence_can_qualify_candidate_only_with_supported_mandatory_requirements():
    result = evaluate_candidate_job(
        job={"canonical_title": "Operations Manager", "application_url": "https://example.test/apply", "lifecycle_status": "ACTIVE", "freshness_status": "FRESH", "application_route_verification": {"status": "ACTIVE", "checked_url": "https://example.test/apply"}, "metadata": {}},
        job_description=("Responsibilities include managing delivery and coordinating teams. Requirements include relevant experience and analytical skills. ") * 5,
        description_version_id="jd-1",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved evidence: managed delivery teams."}],
        preferences={},
        semantic_matches=[
            {"requirement_id": "REQ-001", "requirement_text": "Manage delivery", "status": "direct", "rationale": "Approved evidence directly supports this requirement.", "evidence_refs": ["ev-1"], "mandatory": True},
            {"requirement_id": "REQ-002", "requirement_text": "Coordinate teams", "status": "transferable", "rationale": "Approved evidence supports transferable team coordination.", "evidence_refs": ["ev-1"], "mandatory": False},
        ],
    )
    assert result["outcome"] == "APPLY"
    assert result["score"] == 100
    assert result["evidence_summary"]["dimension_assessments"]["core_role_fit"]["status"] == "UNKNOWN"
    assert result["evidence_summary"]["dimension_assessments"]["application_accessibility"]["status"] == "VERIFIED"
    assert result["evidence_summary"]["dimension_assessments"]["job_freshness"]["status"] == "CURRENT"


def test_semantic_match_cannot_claim_support_without_approved_evidence_reference():
    result = evaluate_candidate_job(
        job={"canonical_title": "Operations Manager", "application_url": "https://example.test/apply", "metadata": {}},
        job_description=("Responsibilities include managing delivery and coordinating teams. Requirements include relevant experience and analytical skills. ") * 5,
        description_version_id="jd-1",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved evidence."}],
        preferences={},
        semantic_matches=[
            {"requirement_id": "REQ-001", "requirement_text": "Manage delivery", "status": "direct", "rationale": "Evidence supports this.", "evidence_refs": ["foreign-id"], "mandatory": True},
        ],
    )
    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert result["explanation"]["confidence"] == "LOW"



def test_dimension_assessments_report_separate_fit_coverage_and_evidence_gaps():
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
            "Requirements include technical architecture, SQL, and a relevant degree. "
        ) * 4,
        description_version_id="jd-dimensions",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved technical architecture experience."}],
        preferences={},
        semantic_matches=[
            {
                "requirement_id": "REQ-001",
                "requirement_text": "Technical architecture",
                "status": "DIRECT",
                "rationale": "Supported by approved evidence.",
                "evidence_refs": ["ev-1"],
                "mandatory": True,
                "dimension": "TECHNICAL",
            },
            {
                "requirement_id": "REQ-002",
                "requirement_text": "SQL",
                "status": "MISSING",
                "rationale": "No approved evidence found.",
                "evidence_refs": [],
                "mandatory": False,
                "dimension": "technical_fit",
            },
            {
                "requirement_id": "REQ-003",
                "requirement_text": "Relevant degree",
                "status": "REQUIRES_VERIFICATION",
                "rationale": "Education evidence needs verification.",
                "evidence_refs": [],
                "mandatory": False,
                "dimension": "EDUCATION",
            },
            {
                "requirement_id": "REQ-004",
                "requirement_text": "Own product roadmap delivery",
                "status": "DIRECT",
                "rationale": "Supported by approved evidence.",
                "evidence_refs": ["ev-1"],
                "mandatory": False,
                "dimension": "CORE_ROLE",
            },
            {
                "requirement_id": "REQ-005",
                "requirement_text": "Healthcare domain experience",
                "status": "DIRECT",
                "rationale": "Supported by approved evidence.",
                "evidence_refs": ["ev-1"],
                "mandatory": False,
                "dimension": "DOMAIN",
            },
            {
                "requirement_id": "REQ-006",
                "requirement_text": "Director-level scope",
                "status": "MISSING",
                "rationale": "No approved evidence found.",
                "evidence_refs": [],
                "mandatory": False,
                "dimension": "SENIORITY",
            },
            {
                "requirement_id": "REQ-007",
                "requirement_text": "Eligible to work in the listed country",
                "status": "DIRECT",
                "rationale": "Supported by approved evidence.",
                "evidence_refs": ["ev-1"],
                "mandatory": False,
                "dimension": "LOCATION",
            },
            {
                "requirement_id": "REQ-008",
                "requirement_text": "Additional contextual requirement",
                "status": "MISSING",
                "rationale": "No approved evidence found.",
                "evidence_refs": [],
                "mandatory": False,
                "dimension": "OTHER",
            },
        ],
    )
    dimensions = result["evidence_summary"]["dimension_assessments"]
    assert dimensions["technical_fit"]["status"] == "PARTIAL"
    assert dimensions["technical_fit"]["score"] == 50
    assert dimensions["technical_fit"]["evidence_refs"] == ["ev-1"]
    assert dimensions["education_fit"]["status"] == "REQUIRES_VERIFICATION"
    assert dimensions["core_role_fit"]["status"] == "MET"
    assert dimensions["domain_fit"]["status"] == "MET"
    assert dimensions["seniority_fit"]["status"] == "UNMET"
    assert dimensions["location_work_authorization"]["status"] == "MET"
    assert dimensions["location_work_authorization"]["requirement_assessment"]["status"] == "MET"
    assert dimensions["other_fit"]["status"] == "UNMET"
    assert dimensions["evidence_quality_completeness"]["status"] == "PARTIAL"
    # Crosswalk every PRD matching dimension so a future refactor cannot silently
    # omit a dimension from the candidate-facing evaluation contract.
    expected_dimensions = {
        "core_role_fit",
        "domain_fit",
        "technical_fit",
        "seniority_fit",
        "education_fit",
        "location_work_authorization",
        "preference_fit",
        "application_accessibility",
        "job_freshness",
        "evidence_quality_completeness",
        "job_lifecycle",
    }
    assert expected_dimensions <= dimensions.keys()
    assert all("status" in dimensions[key] for key in expected_dimensions)
    assert result["evidence_summary"]["unmet_requirements"][0]["status"] == "MISSING"
    assert result["evidence_summary"]["requirements_requiring_verification"][0]["requirement_id"] == "REQ-003"


@pytest.mark.parametrize(
    ("status", "expected_state", "supported"),
    [
        ("direct", "DIRECT", True),
        ("strong_transferable", "STRONG_TRANSFERABLE", True),
        ("transferable", "TRANSFERABLE", True),
        ("partial", "PARTIAL", False),
        ("weak_transferable", "WEAK_TRANSFERABLE", False),
        ("missing", "MISSING", False),
        ("contradicted", "CONTRADICTED", False),
        ("unverified", "REQUIRES_VERIFICATION", False),
        ("disqualifying", "DISQUALIFYING", False),
    ],
)
def test_all_nine_evidence_match_states_have_explicit_deterministic_disposition(
    status, expected_state, supported,
):
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
            "Requirements include technical planning, stakeholder management, and delivery experience. "
        ) * 4,
        description_version_id="jd-nine-match-states",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved evidence for product delivery."}],
        preferences={},
        semantic_matches=[
            {
                "requirement_id": "REQ-001",
                "requirement_text": "Product delivery",
                "status": status,
                "rationale": "Table-driven state contract.",
                "evidence_refs": ["ev-1"] if supported else [],
                "mandatory": False,
            },
        ],
    )

    summary = result["evidence_summary"]
    assert summary["requirement_match_state_counts"][expected_state] == 1
    assert summary["supported_requirement_count"] == int(supported)
    assert len(summary["requirement_matches"]) == 1
    match = summary["requirement_matches"][0]
    assert match["status"] == expected_state
    assert match["rationale"] == "Table-driven state contract."
    if supported:
        assert match["evidence_refs"] == ["ev-1"]
        assert not any(
            item["requirement_id"] == "REQ-001"
            for item in summary["unmet_requirements"]
        )
        assert not any(
            item["requirement_id"] == "REQ-001"
            for item in summary["requirements_requiring_verification"]
        )
    elif expected_state == "REQUIRES_VERIFICATION":
        assert any(
            item["requirement_id"] == "REQ-001"
            for item in summary["requirements_requiring_verification"]
        )
    else:
        assert any(
            item["requirement_id"] == "REQ-001"
            for item in summary["unmet_requirements"]
        )
    if expected_state == "DISQUALIFYING":
        assert result["outcome"] == "REVIEW"


def test_unknown_evidence_match_state_fails_closed_without_scoring():
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
            "Requirements include technical planning, stakeholder management, and delivery experience. "
        ) * 4,
        description_version_id="jd-unknown-match-state",
        description_completeness="COMPLETE",
        candidate_evidence=[{"id": "ev-1", "content": "Approved evidence for product delivery."}],
        preferences={},
        semantic_matches=[
            {
                "requirement_id": "REQ-001",
                "requirement_text": "Product delivery",
                "status": "MAYBE",
                "rationale": "Unsupported status must not be inferred.",
                "evidence_refs": ["ev-1"],
                "mandatory": True,
            },
        ],
    )
    assert result["outcome"] == "REVIEW"
    assert result["score"] is None
    assert result["evidence_summary"]["requirement_matches"] == []
