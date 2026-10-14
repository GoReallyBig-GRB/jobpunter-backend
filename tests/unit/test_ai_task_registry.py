import pytest

from app.application.ai.schemas import TaskType
from app.application.ai.task_registry import AI_TASK_REGISTRY, get_ai_task_definition


def test_registry_covers_every_public_ai_task():
    tasks = TaskType.__args__
    assert set(AI_TASK_REGISTRY) == set(tasks)
    expected_policy_versions = {
        "document_parsing": "v2",
        "claim_evidence_validation": "v2",
    }
    for task in tasks:
        definition = get_ai_task_definition(task)
        assert definition.task_id == task
        assert definition.system_instruction.strip()
        assert definition.policy_version == expected_policy_versions.get(task, "v1")
        assert 0 <= definition.temperature <= 1
        assert definition.input_schema is not None
        assert definition.input_schema["type"] == "object"
        assert definition.input_schema["properties"]
        assert definition.output_schema["type"] == "object"
        assert definition.output_schema["properties"]


def test_registry_is_immutable():
    with pytest.raises(TypeError):
        AI_TASK_REGISTRY["cv_generation"] = AI_TASK_REGISTRY["cover_letter"]


def test_unknown_task_fails_closed():
    with pytest.raises(ValueError, match="Unknown AI task"):
        get_ai_task_definition("unregistered_task")
