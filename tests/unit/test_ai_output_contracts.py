import pytest
from pydantic import ValidationError

from app.application.ai.output_contracts import (
    InvalidTaskOutput,
    TASK_OUTPUT_CONTRACT_VERSIONS,
    _TASK_OUTPUT_ADAPTERS,
    get_task_output_contract_version,
    provider_json_schema,
    validate_task_output,
)
from app.application.ai.schemas import TaskType


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


def test_suggested_answers_contract_requires_evidence_confidence_and_claim_warnings():
    result = validate_task_output(
        "suggested_answers",
        {"answers": [{
            "question": "Why this role?",
            "answer": "Supported by evidence.",
            "evidence_basis": ["candidate-evidence-17"],
            "confidence": "medium",
            "unsupported_claims": [],
        }]},
    )
    assert result == {
        "answers": [{
            "question": "Why this role?",
            "answer": "Supported by evidence.",
            "evidence_basis": ["candidate-evidence-17"],
            "confidence": "medium",
            "unsupported_claims": [],
        }]
    }


def test_suggested_answers_contract_rejects_missing_required_claim_metadata():
    with pytest.raises(InvalidTaskOutput):
        validate_task_output(
            "suggested_answers",
            {"answers": [{"question": "Why this role?", "answer": "Supported by evidence."}]},
        )


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


def test_application_question_contract_includes_confidence_and_claim_warnings():
    result = validate_task_output(
        "application_questions",
        {"questions": [{
            "question": "Why are you interested?",
            "suggested_answer": "I am interested in the role.",
            "evidence_basis": ["candidate-evidence-1"],
            "confidence": "medium",
            "unsupported_claims": [],
            "missing_information": [],
        }]},
    )
    question = result["questions"][0]
    assert question["confidence"] == "medium"
    assert question["unsupported_claims"] == []


def test_suggested_answer_contract_flags_unsupported_claims():
    result = validate_task_output(
        "suggested_answers",
        {"answers": [{
            "question": "Have you led a team?",
            "answer": "I led a team of ten.",
            "evidence_basis": [],
            "confidence": "low",
            "unsupported_claims": ["Team size is not supported by supplied evidence."],
            "missing_information": [],
        }]},
    )
    assert result["answers"][0]["unsupported_claims"]



def test_claim_evidence_validation_contract_requires_atomic_claim_checks():
    result = validate_task_output("claim_evidence_validation", {
        "checks": [{
            "artifact_type": "cover_letter",
            "claim_text": "Led product delivery.",
            "status": "supported",
            "evidence_refs": ["candidate-evidence-17"],
            "rationale": "Approved project evidence documents product delivery leadership.",
            "confidence": "high",
        }],
        "audited_artifact_types": ["cover_letter"],
        "unassessed_claims": [],
        "coverage_complete": True,
    }, {
        "artifacts": [{
            "artifact_type": "cover_letter",
            "content": "Led product delivery.",
            "declared_evidence_refs": ["candidate-evidence-17"],
            "declared_unsupported_claims": [],
        }],
        "approved_candidate_evidence": [{
            "id": "candidate-evidence-17",
            "type": "experience",
            "content": {"summary": "Led product delivery."},
            "verified_at": None,
        }],
    })
    assert result["checks"][0]["status"] == "supported"


@pytest.mark.parametrize("invalid", [
    {"checks": [], "audited_artifact_types": ["cover_letter"], "unassessed_claims": [], "coverage_complete": True},
    {"checks": [{"artifact_type": "cv", "claim_text": "Claim", "status": "supported",
                 "evidence_refs": [], "rationale": "Reason", "confidence": "high"}],
     "audited_artifact_types": ["cover_letter"], "unassessed_claims": [], "coverage_complete": True},
    {"checks": [{"artifact_type": "cover_letter", "claim_text": "Claim", "status": "invented",
                 "evidence_refs": [], "rationale": "Reason", "confidence": "high"}],
     "audited_artifact_types": ["cover_letter"], "unassessed_claims": [], "coverage_complete": True},
])
def test_claim_evidence_validation_contract_rejects_invalid_results(invalid):
    with pytest.raises(InvalidTaskOutput):
        validate_task_output("claim_evidence_validation", invalid)


@pytest.mark.parametrize("task", sorted(_TASK_OUTPUT_ADAPTERS))
def test_every_registered_task_rejects_an_empty_output_object(task):
    with pytest.raises(InvalidTaskOutput):
        validate_task_output(task, {})


@pytest.mark.parametrize(
    ("task", "payload"),
    [
        ("document_parsing", {"document_type": "   ", "fields": []}),
        ("profile_extraction", {"fields": [], "summary": "   "}),
        (
            "jd_extraction",
            {
                "job_title": "   ",
                "company": None,
                "location": None,
                "employment_type": None,
                "responsibilities": [],
                "requirements": [],
                "eligibility_conditions": [],
                "application_instructions": [],
            },
        ),
    ],
)
def test_required_contract_strings_reject_whitespace_only_values(task, payload):
    with pytest.raises(InvalidTaskOutput):
        validate_task_output(task, payload)


@pytest.mark.parametrize("task", sorted(_TASK_OUTPUT_ADAPTERS))
def test_every_task_provider_schema_has_consistent_required_properties(task):
    schema = provider_json_schema(task)
    assert schema["type"] == "object"
    properties = schema["properties"]
    assert properties
    assert set(schema["required"]).issubset(properties)


def test_claim_validation_cannot_mark_coverage_complete_with_unassessed_claims():
    with pytest.raises(InvalidTaskOutput):
        validate_task_output("claim_evidence_validation", {
            "checks": [{
                "artifact_type": "cover_letter",
                "claim_text": "A factual claim that was assessed.",
                "status": "supported",
                "evidence_refs": ["candidate-evidence-17"],
                "rationale": "Supported by approved candidate evidence.",
                "confidence": "high",
            }],
            "audited_artifact_types": ["cover_letter"],
            "unassessed_claims": ["A second factual claim was not assessed."],
            "coverage_complete": True,
        })


def test_claim_validation_requires_explicit_unassessed_claims_list():
    with pytest.raises(InvalidTaskOutput):
        validate_task_output("claim_evidence_validation", {
            "checks": [{
                "artifact_type": "cover_letter",
                "claim_text": "A factual claim.",
                "status": "supported",
                "evidence_refs": ["candidate-evidence-17"],
                "rationale": "Supported by approved candidate evidence.",
                "confidence": "high",
            }],
            "audited_artifact_types": ["cover_letter"],
            "coverage_complete": True,
        })


def test_claim_validation_rejects_duplicate_audited_artifact_types():
    with pytest.raises(InvalidTaskOutput):
        validate_task_output("claim_evidence_validation", {
            "checks": [{
                "artifact_type": "cover_letter",
                "claim_text": "A factual claim.",
                "status": "unsupported",
                "evidence_refs": [],
                "rationale": "No approved evidence supports the claim.",
                "confidence": "low",
            }],
            "audited_artifact_types": ["cover_letter", "cover_letter"],
            "unassessed_claims": [],
            "coverage_complete": True,
        })


def test_every_registered_task_has_a_versioned_output_contract():
    assert set(TASK_OUTPUT_CONTRACT_VERSIONS) == set(_TASK_OUTPUT_ADAPTERS)
    assert {
        task: get_task_output_contract_version(task)
        for task in _TASK_OUTPUT_ADAPTERS
    } == {task: "v1" for task in _TASK_OUTPUT_ADAPTERS}


def test_unknown_task_has_no_output_contract_version():
    with pytest.raises(ValueError, match="Unknown AI task output contract version"):
        get_task_output_contract_version("unregistered_task")
