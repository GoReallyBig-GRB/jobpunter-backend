from typing import get_args

from app.application.ai.schemas import TaskType
from app.infrastructure.ai.google_generation import TASK_SYSTEM_INSTRUCTIONS, TASK_TEMPERATURE


def test_every_canonical_ai_task_has_temperature_and_server_policy():
    canonical_tasks = set(get_args(TaskType))
    assert set(TASK_TEMPERATURE) == canonical_tasks
    assert set(TASK_SYSTEM_INSTRUCTIONS) == canonical_tasks
    assert all(instruction.strip() for instruction in TASK_SYSTEM_INSTRUCTIONS.values())
