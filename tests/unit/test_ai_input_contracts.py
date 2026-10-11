import json
import pytest
from app.application.ai.input_contracts import InvalidTaskInput, validate_task_input


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
