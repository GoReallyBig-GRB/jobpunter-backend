import pytest
from pydantic import ValidationError

from app.application.ai.output_contracts import (
    EvidenceMatchingOutput,
    InvalidTaskOutput,
    validate_task_output,
)


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
    assert isinstance(result, EvidenceMatchingOutput)
    assert result.matches[0].status.value == "transferable"
    assert result.matches[0].evidence_refs == ["candidate-evidence-17"]


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


def test_unknown_task_does_not_accidentally_apply_evidence_matching_schema():
    value = {"arbitrary": ["task-specific output not yet governed"]}
    assert validate_task_output("cover_letter", value) is value
