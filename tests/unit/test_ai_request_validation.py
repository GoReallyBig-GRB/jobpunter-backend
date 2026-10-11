import pytest
from pydantic import ValidationError

from app.application.ai.schemas import GenerateRequest


@pytest.mark.parametrize("prompt", ["", " ", "\t", "\n  \t"])
def test_ai_request_rejects_empty_or_whitespace_only_prompt(prompt):
    with pytest.raises(ValidationError):
        GenerateRequest(task="cv_generation", prompt=prompt)


@pytest.mark.parametrize("description", ["", " ", "\t", "\n  \t"])
def test_ai_request_rejects_empty_or_whitespace_only_schema_description(description):
    with pytest.raises(ValidationError):
        GenerateRequest(
            task="cv_generation",
            prompt="Generate using only the supplied evidence.",
            schema_description=description,
        )


def test_ai_request_preserves_nonblank_prompt_and_schema_description():
    request = GenerateRequest(
        task="cv_generation",
        prompt="  Generate using only the supplied evidence.  ",
        schema_description=" Return a JSON object. ",
    )
    assert request.prompt == "  Generate using only the supplied evidence.  "
    assert request.schema_description == " Return a JSON object. "
