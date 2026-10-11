from typing import get_args

from app.application.ai.schemas import TaskType
from app.application.ai.task_registry import AI_TASK_REGISTRY


def test_every_canonical_ai_task_has_temperature_and_server_policy():
    canonical_tasks = set(get_args(TaskType))
    assert set(AI_TASK_REGISTRY) == canonical_tasks
    assert all(definition.system_instruction.strip() for definition in AI_TASK_REGISTRY.values())
    assert all(0 <= definition.temperature <= 1 for definition in AI_TASK_REGISTRY.values())
