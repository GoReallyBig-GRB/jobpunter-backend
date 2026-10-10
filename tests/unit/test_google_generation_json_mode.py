import asyncio
from types import SimpleNamespace

import pytest

from app.infrastructure.ai import google_generation


class FakeModels:
    def __init__(self, response_text):
        self.response_text = response_text
        self.last_call = None

    async def generate_content(self, **kwargs):
        self.last_call = kwargs
        return SimpleNamespace(text=self.response_text)


class FakeClient:
    def __init__(self, response_text, **_kwargs):
        self.models = FakeModels(response_text)
        self.aio = SimpleNamespace(models=self.models)


def run_generation(monkeypatch, response_text, *, json_mode, schema_description=None):
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "unit-test-key")
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_MODEL", "gemini-test")
    monkeypatch.setattr(
        google_generation.genai,
        "Client",
        lambda **kwargs: FakeClient(response_text, **kwargs),
    )
    return asyncio.run(google_generation.generate_with_google(
        task="suggested_answers",
        prompt="Return the supplied test data.",
        schema_description=schema_description,
        json_mode=json_mode,
        max_tokens=128,
    ))


def test_json_mode_parses_json_without_schema_description(monkeypatch):
    result = run_generation(
        monkeypatch,
        '{"answers": [{"question": "Why this role?", "answer": "Supported by evidence."}]}',
        json_mode=True,
    )
    assert result.parsed_data == {
        "answers": [{"question": "Why this role?", "answer": "Supported by evidence."}]
    }
    assert result.raw_text.startswith("{")
    assert result.provider_used == "google"


def test_json_mode_rejects_invalid_json_without_schema_description(monkeypatch):
    with pytest.raises(google_generation.InvalidStructuredOutput):
        run_generation(
            monkeypatch,
            "This is plain text, not JSON.",
            json_mode=True,
        )


def test_free_text_mode_remains_unparsed_without_schema_description(monkeypatch):
    result = run_generation(
        monkeypatch,
        "A readable cover letter paragraph.",
        json_mode=False,
    )
    assert result.raw_text == "A readable cover letter paragraph."
    assert result.parsed_data is None


def test_schema_description_still_requires_valid_json(monkeypatch):
    with pytest.raises(google_generation.InvalidStructuredOutput):
        run_generation(
            monkeypatch,
            '{"answers": ',
            json_mode=False,
            schema_description="Return an object with an answers array.",
        )

@pytest.mark.parametrize("response_text", ["NaN", "Infinity", '{"score": NaN}'])
def test_json_mode_rejects_nonstandard_json_constants(monkeypatch, response_text):
    with pytest.raises(google_generation.InvalidStructuredOutput):
        run_generation(monkeypatch, response_text, json_mode=True)


@pytest.mark.parametrize("response_text", ['"a scalar string"', "42", "true", "null"])
def test_json_mode_rejects_scalar_root_values(monkeypatch, response_text):
    with pytest.raises(google_generation.InvalidStructuredOutput):
        run_generation(monkeypatch, response_text, json_mode=True)


def test_json_mode_accepts_array_root(monkeypatch):
    result = run_generation(monkeypatch, '[{"requirement": "Python"}]', json_mode=True)
    assert result.parsed_data == [{"requirement": "Python"}]
