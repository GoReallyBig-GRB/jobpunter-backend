from app.application.jobs.requirement_evaluation import (
    extract_requirements_deterministically,
    map_requirements_to_evidence,
)


def test_deterministic_extraction_preserves_title_and_classifies_requirements():
    results = extract_requirements_deterministically(
        "Required: Experience with REST APIs and SQL data models\n"
        "- Must be located in Nigeria and have valid work authorization",
        "Technical Product Manager",
    )
    assert results[0] == {
        "id": "req_det_1",
        "requirement": "Core role focus: Technical Product Manager",
        "category": "ROLE_CORE",
        "severity": "IMPORTANT",
        "confidence": "HIGH",
    }
    assert results[1]["category"] == "TECHNICAL"
    assert results[1]["severity"] == "MUST_HAVE"
    assert results[2]["category"] == "LOCATION"
    assert results[2]["severity"] == "MUST_HAVE"


def test_deterministic_extraction_caps_results_and_emits_fallback():
    lines = "\n".join(
        f"- Experience with requirement area number {i} and its related delivery work"
        for i in range(1, 15)
    )
    assert len(extract_requirements_deterministically(lines, "Product Manager")) == 8
    fallback = extract_requirements_deterministically("Short text", None)
    assert fallback[0]["confidence"] == "LOW"
    assert fallback[0]["severity"] == "IMPORTANT"


def test_evidence_mapping_is_candidate_specific_and_does_not_use_hardcoded_projects():
    requirements = [{
        "id": "req_api",
        "requirement": "Experience with API integrations and SQL data models",
        "severity": "MUST_HAVE",
        "category": "TECHNICAL",
    }]
    profile_with_evidence = {
        "primaryProfession": "Technical Product Manager",
        "rawText": "Delivered API integrations and SQL data models.",
        "structuredData": {
            "projectsOrProducts": [{
                "name": "Candidate A Project",
                "context": "API integrations and SQL data models",
                "bullets": ["Defined REST API contracts"],
                "capabilities": ["SQL"],
                "keywords": ["integration"],
            }]
        },
    }
    profile_without_evidence = {
        "primaryProfession": "Marketing Coordinator",
        "rawText": "Created social campaigns and event calendars.",
        "structuredData": {
            "projectsOrProducts": [{
                "name": "Candidate B Project",
                "context": "Social campaigns and event calendars",
                "bullets": ["Managed editorial schedules"],
                "capabilities": ["Content marketing"],
                "keywords": ["events"],
            }]
        },
    }

    first = map_requirements_to_evidence(requirements, profile_with_evidence)[0]
    second = map_requirements_to_evidence(requirements, profile_without_evidence)[0]

    assert first["status"] == "DIRECT"
    assert first["sourceProduct"] == "Candidate A Project"
    assert second["status"] == "MISSING"
    assert second["isMaterialGap"] is True
    assert "MarketGrowthOS" not in first["candidateEvidence"]
    assert "MarketGrowthOS" not in second["candidateEvidence"]


def test_candidate_exclusions_are_read_from_profile_not_global_sales_rules():
    requirement = [{
        "id": "req_sales",
        "requirement": "Quota-carrying sales commission and outbound SDR work",
        "severity": "MUST_HAVE",
    }]
    excludes_sales = {
        "primaryProfession": "Product Manager",
        "preferences": {"excludedJobTypes": ["sales", "SDR"]},
    }
    no_sales_exclusion = {
        "primaryProfession": "Sales Manager",
        "rawText": "Managed a quota-carrying sales team and SDR program.",
    }

    blocked = map_requirements_to_evidence(requirement, excludes_sales)[0]
    not_globally_blocked = map_requirements_to_evidence(requirement, no_sales_exclusion)[0]

    assert blocked["status"] == "DISQUALIFYING"
    assert blocked["isMaterialGap"] is True
    assert not_globally_blocked["status"] == "DIRECT"
