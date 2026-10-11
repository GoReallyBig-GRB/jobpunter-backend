from app.application.opportunities.evaluation_policy import evaluate_candidate_job


def test_semantic_evidence_can_qualify_candidate_only_with_supported_mandatory_requirements():
    result = evaluate_candidate_job(
        job={"canonical_title": "Operations Manager", "application_url": "https://example.test/apply", "metadata": {}},
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
