import pytest

from app.application.opportunities.evaluation_policy import evaluate_candidate_job
from app.application.opportunities.semantic_matching import _requirement_dimension


def test_semantic_evidence_can_qualify_candidate_only_with_supported_mandatory_requirements():
    result = evaluate_candidate_job(
        job={"canonical_title": "Operations Manager", "application_url": "https://example.test/apply", "lifecycle_status": "ACTIVE", "freshness_status": "FRESH", "metadata": {}},
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
                "dimension": "technical_fit",
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
                "dimension": "education_fit",
            },
        ],
    )
    dimensions = result["evidence_summary"]["dimension_assessments"]
    assert dimensions["technical_fit"]["status"] == "PARTIAL"
    assert dimensions["technical_fit"]["score"] == 50
    assert dimensions["technical_fit"]["evidence_refs"] == ["ev-1"]
    assert dimensions["education_fit"]["status"] == "REQUIRES_VERIFICATION"
    assert dimensions["seniority_fit"]["status"] == "UNKNOWN"
    assert dimensions["location_work_authorization"]["status"] == "UNKNOWN"



@pytest.mark.parametrize(
    ("category", "text", "expected"),
    [
        ("education", "Bachelor degree required", "education_fit"),
        ("work_authorization", "Must be authorized to work", "location_work_authorization"),
        ("seniority", "Ten years of experience", "seniority_fit"),
        ("domain", "Experience in the healthcare industry", "domain_fit"),
        ("technical", "Experience with SQL and APIs", "technical_fit"),
        ("responsibility", "Own product roadmap delivery", "core_role_fit"),
    ],
)
def test_extracted_requirements_map_to_prd_fit_dimensions(category, text, expected):
    assert _requirement_dimension(category, text) == expected
