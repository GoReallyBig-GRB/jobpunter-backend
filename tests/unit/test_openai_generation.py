import asyncio
from types import SimpleNamespace

from app.infrastructure.ai import openai_generation


def test_openai_adapter_uses_shared_task_policy_and_parses_json(monkeypatch):
    calls = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"content":"supported"}'))])

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())
        async def close(self):
            return None

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", FakeClient)
    monkeypatch.setenv("JOBPUNTER_OPENAI_API_KEY", "test-key")
    result = asyncio.run(openai_generation.generate_with_openai(
        task="cover_letter",
        prompt='{"approved_evidence":[]}',
        schema_description=None,
        json_mode=True,
        max_tokens=100,
        model_override="gpt-4.1-mini",
    ))
    assert result.provider_used == "openai"
    assert result.model_used == "gpt-4.1-mini"
    assert result.parsed_data == {"content": "supported"}
    assert calls["response_format"] == {"type": "json_object"}
    assert "Never invent candidate facts" in calls["messages"][0]["content"]
