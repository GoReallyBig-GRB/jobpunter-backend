from app.application.jobs.normalization import evaluate_seniority


def test_seniority_flags_executive_roles_with_extreme_scope_as_hard_no_go():
    result = evaluate_seniority(
        "Director of Product",
        "Manage a team of 20+ across multiple management tiers; 15+ years required.",
        {"primaryProfession": "Product Manager", "structuredData": {"leadershipOwnership": ["Owns product delivery"]}},
        {"targetSeniority": ["Senior Product Manager"]},
    )
    assert result["isMaterial"] is True
    assert result["score"] == 2
    assert result["decisionImpact"].startswith("HARD_NO_GO")


def test_seniority_requires_review_for_executive_title_without_scope_evidence():
    result = evaluate_seniority(
        "Head of Product",
        "Hands-on product leadership",
        {"primaryProfession": "Product Manager", "structuredData": {}},
        {"targetSeniority": ["Senior Product Manager"]},
    )
    assert result["isMaterial"] is False
    assert result["score"] == 8
    assert result["decisionImpact"].startswith("REVIEW")


def test_seniority_uses_target_preference_for_nonexecutive_titles():
    result = evaluate_seniority(
        "Senior Product Manager",
        "Lead product discovery and delivery",
        {"primaryProfession": "Product Manager", "structuredData": {"leadershipOwnership": ["Roadmap"]}},
        {"targetSeniority": ["Senior Product Manager"]},
    )
    assert result["score"] == 15
    assert result["isMaterial"] is False
    assert result["seniorityGap"].startswith("Direct match")
