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
    job = {"canonical_title": "Technical Product Manager", "metadata": {}}
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
