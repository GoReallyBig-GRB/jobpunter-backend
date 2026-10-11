"""Provider-neutral contracts for governed AI execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResult:
    raw_text: str
    parsed_data: Any | None
    model_used: str
    provider_used: str
    latency_ms: int


class AIProviderNotConfigured(RuntimeError):
    """The selected provider is not configured or is not registered."""


class AIProviderFailure(RuntimeError):
    """The selected provider failed to complete the request."""


class InvalidStructuredOutput(RuntimeError):
    """A provider returned invalid structured output."""


class AIProviderAdapter(Protocol):
    async def generate(
        self,
        *,
        task: str,
        prompt: str,
        schema_description: str | None,
        json_mode: bool,
        max_tokens: int,
        model_override: str | None = None,
        task_instruction_override: str | None = None,
        temperature_override: float | None = None,
    ) -> ProviderResult: ...
