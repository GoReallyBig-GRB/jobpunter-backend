import pytest
from pydantic import ValidationError

from app.application.ai.output_contracts import (\n    InvalidTaskOutput,\n    _TASK_OUTPUT_ADAPTERS,\n    provider_json_schema,\n    validate_task_output,\n)\nfrom app.application.ai.schemas import TaskType


def valid_output():
    return {
        "matches": [
            {
                "requirement_id": "REQ-001",
                "requirement_text": "Three years of product management experience",
                "status": "transferable",
                "rationale": "The candidate has documented delivery leadership but the exact duration is unverified.",
                "evidence_refs": ["candidate-evidence-17"],
            }
        ]
    }


def test_evidence_matching_output_accepts_registered_status_and_evidence_refs():
    result = validate_task_output("evidence_matching", valid_output())
    assert result["matches"][0]["status"] == "transferable"
    assert result["matches"][0]["evidence_refs"] == ["candidate-evidence-17"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["matches"][0].update(status="strong_match"),
        lambda data: data["matches"][0].pop("evidence_refs"),
        lambda data: data["matches"][0].update(rationale="  "),
        lambda data: data["matches"][0].update(unreviewed_claim=True),
        lambda data: data.update(matches=[]),
        lambda data: data["matches"][0].update(requirement_id=""),
    ],
)
def test_evidence_matching_output_rejects_invalid_or_incomplete_contract(mutate):
    data = valid_output()
    mutate(data)
    with pytest.raises(InvalidTaskOutput):
        validate_task_output("evidence_matching", data)


def test_every_supported_task_has_a_server_owned_output_contract():
    assert set(_TASK_OUTPUT_ADAPTERS) == set(TaskType.__args__)


def test_suggested_answers_contract_preserves_existing_minimal_response_shape():
    result = validate_task_output(
        "suggested_answers",
        {"answers": [{"question": "Why this role?", "answer": "Supported by evidence."}]},
    )
    assert result == {
        "answers": [{"question": "Why this role?", "answer": "Supported by evidence."}]
    }


def test_cover_letter_contract_rejects_unknown_fields_and_missing_content():
    with pytest.raises(InvalidTaskOutput):
        validate_task_output("cover_letter", {"answer": "Not the registered contract"})


def test_provider_schema_uses_supported_json_schema_keywords():
    schema = provider_json_schema("evidence_matching")
    assert schema["type"] == "object"
    assert "matches" in schema["properties"]
    assert "minLength" not in str(schema)


def test_unknown_task_does_not_accidentally_apply_another_task_schema():
    value = {"arbitrary": ["task-specific output not yet governed"]}
    assert validate_task_output("not_a_registered_task", value) is value
