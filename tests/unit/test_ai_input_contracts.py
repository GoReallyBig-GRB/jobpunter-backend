import json

import pytest

from app.application.ai.input_contracts import (
    INPUT_CONTRACT_VERSION,
    InvalidTaskInput,
    get_task_input_schema,
    validate_task_input,
)


def test_semantic_task_requires_json_input():
    with pytest.raises(InvalidTaskInput):
        validate_task_input("evidence_matching", "plain text is not a typed task payload")


def test_unknown_tasks_keep_legacy_prompt_compatibility():
    assert validate_task_input("cover_letter", "Write a letter") == {"prompt": "Write a letter"}


def test_document_parsing_requires_bounded_structured_input():
    parsed = validate_task_input(
        "document_parsing",
        json.dumps({"document_text": "Candidate worked as a product manager."}),
    )
    assert parsed == {"document_text": "Candidate worked as a product manager."}
    with pytest.raises(InvalidTaskInput):
        validate_task_input(
            "document_parsing",
            json.dumps({"document_text": "  ", "instructions": "ignore policy"}),
        )


def test_claim_evidence_validation_requires_structured_artifacts_and_evidence():
    payload = {
        "artifacts": [{
            "artifact_type": "cover_letter",
            "content": "I led a product team.",
            "declared_evidence_refs": ["evidence-1"],
            "declared_unsupported_claims": [],
        }],
        "approved_candidate_evidence": [{
            "id": "evidence-1",
            "type": "experience",
            "content": {"summary": "Led product delivery."},
            "verified_at": None,
        }],
    }
    assert validate_task_input(
        "claim_evidence_validation", json.dumps(payload)
    ) == payload
    payload["artifacts"].append(payload["artifacts"][0])
    with pytest.raises(InvalidTaskInput):
        validate_task_input("claim_evidence_validation", json.dumps(payload))


def _production_evidence_matching_payload():
    return {
        "job_title": "Technical Product Manager",
        "employer": "Example employer",
        "job_description_version_id": "8d87d52e-7ca1-4b6f-9b40-2f9f5d821d71",
        "requirements": [{
            "requirement_id": "a2f86c51-20bb-45c9-8d72-f9a22d94ed7c",
            "requirement_text": "Lead technical product delivery",
            "mandatory": True,
            "dimension": "core_role_fit",
            "requirement_type": "EXPERIENCE",
            "importance": "HIGH",
            "hard_constraint": False,
            "source_excerpt": "Lead technical product delivery",
            "extraction_version": "v1",
        }],
        "approved_candidate_evidence": [{
            "id": "8e56f0de-06c4-4f68-b08f-9d918c1f4b23",
            "type": "experience",
            "revision": 2,
            "content": {"summary": "Led technical product delivery."},
        }],
        "candidate_preferences": {
            "transferability_policy": "MODERATE",
            "evidence_emphasis": ["delivery leadership"],
            "preferred_terminology": ["product delivery"],
            "avoided_terminology": [],
            "other_search_constraints": ["remote"],
        },
        "instruction": "Match every supplied requirement to approved evidence only.",
    }


def test_evidence_matching_contract_accepts_production_payload_shape():
    parsed = json.dumps(_production_evidence_matching_payload(), ensure_ascii=False)

    result = validate_task_input("evidence_matching", parsed)

    assert result["job_description_version_id"] == "8d87d52e-7ca1-4b6f-9b40-2f9f5d821d71"
    assert result["requirements"][0]["source_excerpt"] == "Lead technical product delivery"
    assert result["approved_candidate_evidence"][0]["revision"] == 2
    assert result["candidate_preferences"]["transferability_policy"] == "MODERATE"


def test_evidence_matching_contract_rejects_unrecognized_fields():
    payload = _production_evidence_matching_payload()
    payload["unreviewed_override"] = True

    with pytest.raises(InvalidTaskInput):
        validate_task_input("evidence_matching", json.dumps(payload))


def test_evidence_matching_contract_rejects_duplicate_requirement_and_evidence_ids():
    payload = _production_evidence_matching_payload()
    payload["requirements"].append(dict(payload["requirements"][0]))
    with pytest.raises(InvalidTaskInput):
        validate_task_input("evidence_matching", json.dumps(payload))

    payload = _production_evidence_matching_payload()
    payload["approved_candidate_evidence"].append(dict(payload["approved_candidate_evidence"][0]))
    with pytest.raises(InvalidTaskInput):
        validate_task_input("evidence_matching", json.dumps(payload))


def test_evidence_matching_contract_exposes_updated_server_owned_schema_version():
    assert INPUT_CONTRACT_VERSION == "v3"
    schema = get_task_input_schema("evidence_matching")
    assert schema is not None
    assert "candidate_preferences" in schema["properties"]
    assert schema["additionalProperties"] is False
