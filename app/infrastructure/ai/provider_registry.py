"""Task execution adapter registry; application services do not import vendor SDKs."""
from __future__ import annotations

import os
from typing import Any, Callable

from app.infrastructure.ai.google_generation import generate_with_google
from app.infrastructure.ai.openai_generation import generate_with_openai
from app.ports.ai import AIProviderNotConfigured, AIProviderAdapter


class _FunctionAdapter:
    def __init__(self, function: Callable[..., Any]):
        self._function = function

    async def generate(self, **kwargs: Any):
        return await self._function(**kwargs)


_PROVIDERS: dict[str, AIProviderAdapter] = {
    "google": _FunctionAdapter(generate_with_google),
    "openai": _FunctionAdapter(generate_with_openai),
}


def get_ai_provider(provider_name: str | None = None) -> AIProviderAdapter:
    name = (provider_name or os.getenv("JOBPUNTER_AI_PROVIDER", "google")).strip().lower()
    adapter = _PROVIDERS.get(name)
    if adapter is None:
        raise AIProviderNotConfigured("The configured AI provider is not supported.")
    return adapter
