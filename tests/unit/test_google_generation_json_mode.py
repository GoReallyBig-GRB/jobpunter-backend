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


def test_provider_failure_logs_only_error_class_and_status(monkeypatch, caplog):
    secret_text = "sensitive job text and unit-test-key"

    class ProviderError(Exception):
        code = 403
        status = "PERMISSION_DENIED"

    class FailingModels:
        async def generate_content(self, **_kwargs):
            raise ProviderError(secret_text)

    class FailingClient:
        def __init__(self, **_kwargs):
            self.aio = SimpleNamespace(models=FailingModels())

    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "unit-test-key")
    monkeypatch.setattr(google_generation.genai, "Client", lambda **kwargs: FailingClient(**kwargs))
    with (
        caplog.at_level("WARNING", logger=google_generation.logger.name),
        pytest.raises(google_generation.AIProviderFailure),
    ):
        asyncio.run(google_generation.generate_with_google(
            task="suggested_answers", prompt=secret_text, schema_description=None,
            json_mode=False, max_tokens=64,
        ))
    assert "error_class=ProviderError http_status=403 api_status=PERMISSION_DENIED" in caplog.text
    assert "sensitive" not in caplog.text and "unit-test-key" not in caplog.text


def test_provider_failure_ignores_unexpected_status_shapes(monkeypatch, caplog):
    class OddError(Exception):
        code = "not-a-number"
        status = "free text that could leak content"

    class FailingModels:
        async def generate_content(self, **_kwargs):
            raise OddError("x")

    class FailingClient:
        def __init__(self, **_kwargs):
            self.aio = SimpleNamespace(models=FailingModels())

    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "unit-test-key")
    monkeypatch.setattr(google_generation.genai, "Client", lambda **kwargs: FailingClient(**kwargs))
    with (
        caplog.at_level("WARNING", logger=google_generation.logger.name),
        pytest.raises(google_generation.AIProviderFailure),
    ):
        asyncio.run(google_generation.generate_with_google(
            task="suggested_answers", prompt="p", schema_description=None,
            json_mode=False, max_tokens=64,
        ))
    assert "error_class=OddError http_status=None api_status=None" in caplog.text


def _run_failing(monkeypatch, caplog, *, task, exc):
    class FailingModels:
        async def generate_content(self, **_kwargs):
            raise exc

    class FailingClient:
        def __init__(self, **_kwargs):
            self.aio = SimpleNamespace(models=FailingModels())

    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "unit-test-key")
    monkeypatch.setattr(google_generation.genai, "Client", lambda **kwargs: FailingClient(**kwargs))
    with (
        caplog.at_level("WARNING", logger=google_generation.logger.name),
        pytest.raises(google_generation.AIProviderFailure),
    ):
        asyncio.run(google_generation.generate_with_google(
            task=task, prompt="p", schema_description=None, json_mode=False, max_tokens=64,
        ))


class RejectedRequest(Exception):
    code = 400
    status = "INVALID_ARGUMENT"
    message = "schema too complex; key unit-test-key " + "x" * 400


def test_public_job_text_task_logs_truncated_redacted_rejection_detail(monkeypatch, caplog):
    _run_failing(monkeypatch, caplog, task="job_requirement_extraction", exc=RejectedRequest("r"))
    assert "google_generation_rejected task=job_requirement_extraction detail=schema too complex" in caplog.text
    assert "unit-test-key" not in caplog.text
    detail = caplog.text.split("detail=", 1)[1].splitlines()[0]
    assert len(detail) <= 300


def test_candidate_data_task_never_logs_provider_message(monkeypatch, caplog):
    _run_failing(monkeypatch, caplog, task="suggested_answers", exc=RejectedRequest("r"))
    assert "google_generation_failed" in caplog.text
    assert "google_generation_rejected" not in caplog.text
    assert "schema too complex" not in caplog.text


def test_server_errors_never_log_provider_message(monkeypatch, caplog):
    class ServerError(Exception):
        code = 503
        status = "UNAVAILABLE"
        message = "overloaded"

    _run_failing(monkeypatch, caplog, task="job_requirement_extraction", exc=ServerError("s"))
    assert "google_generation_rejected" not in caplog.text
