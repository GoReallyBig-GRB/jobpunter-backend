import pytest

from app.infrastructure.ai.provider_registry import get_ai_provider
from app.ports.ai import AIProviderNotConfigured


def test_provider_registry_exposes_independent_google_and_openai_adapters():
    google = get_ai_provider("google")
    openai = get_ai_provider("openai")
    assert google is not openai
    assert callable(google.generate)
    assert callable(openai.generate)


def test_provider_registry_fails_closed_for_unknown_provider():
    with pytest.raises(AIProviderNotConfigured):
        get_ai_provider("unsupported-provider")
