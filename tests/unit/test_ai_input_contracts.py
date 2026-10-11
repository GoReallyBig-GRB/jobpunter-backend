import json
import pytest
from app.application.ai.input_contracts import InvalidTaskInput, validate_task_input


def test_semantic_task_requires_json_input():
    with pytest.raises(InvalidTaskInput):
        validate_task_input("evidence_matching", "plain text is not a typed task payload")


def test_unknown_tasks_keep_legacy_prompt_compatibility():
    assert validate_task_input("cover_letter", "Write a letter") == {"prompt": "Write a letter"}
