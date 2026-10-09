import json

import httpx
import pytest

from app.infrastructure.ai.job_requirement_extractor import (
    OpenAICompatibleJobRequirementExtractor,
    RequirementExtractionError,
)


class FakeResponse:
    def __init__(self, requirements, status_code=200):
        self.payload = {
            "choices": [{
                "message": {"content": json.dumps({"requirements": requirements})}
            }]
        }
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "bad response",
                request=httpx.Request("POST", "https://matcher.example/v1/chat/completions"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def make_extractor(client):
    return OpenAICompatibleJobRequirementExtractor(
        endpoint="https://matcher.example/v1/chat/completions",
        api_key="test-key",
        model="test-model",
        client=client,
    )


def test_extracts_only_requirements_grounded_in_source_excerpt():
    client = FakeClient(FakeResponse([{
        "requirement_type": "SKILL",
        "description": "Experience using SQL in production systems",
        "source_excerpt": "Experience with SQL in production systems",
        "importance": "HIGH",
        "hard_constraint": False,
        "confidence": 0.93,
    }]))
    extractor = make_extractor(client)

    result = extractor(
        title="Technical Product Manager",
        company="Example",
        description="We need a Technical Product Manager. Experience with SQL in production systems.",
    )

    assert len(result) == 1
    assert result[0].requirement_type == "SKILL"
    assert result[0].confidence == 0.93
    request = client.calls[0][1]["json"]
    assert request["temperature"] == 0
    assert request["messages"][1]["content"].find("Experience with SQL") >= 0


def test_rejects_requirement_with_unsubstantiated_excerpt():
    extractor = make_extractor(FakeClient(FakeResponse([{
        "requirement_type": "EDUCATION",
        "description": "Must have a PhD",
        "source_excerpt": "PhD required",
        "importance": "CRITICAL",
        "hard_constraint": True,
        "confidence": 0.99,
    }])))

    with pytest.raises(RequirementExtractionError, match="not grounded"):
        extractor(
            title="Product Manager",
            company=None,
            description="Bachelor's degree or equivalent experience preferred.",
        )


def test_rejects_invalid_requirement_fields():
    extractor = make_extractor(FakeClient(FakeResponse([{
        "requirement_type": "MADE_UP",
        "description": "Something",
        "source_excerpt": "Something",
        "importance": "HIGH",
        "hard_constraint": True,
        "confidence": 0.8,
    }])))

    with pytest.raises(RequirementExtractionError):
        extractor(title="Role", company=None, description="Something")


def test_empty_requirements_is_a_valid_extraction_result():
    extractor = make_extractor(FakeClient(FakeResponse([])))

    assert extractor(title="Role", company=None, description="Company overview only.") == []


def test_provider_payload_redacts_contact_details():
    client = FakeClient(FakeResponse([]))
    extractor = make_extractor(client)

    extractor(
        title="Role",
        company=None,
        description="Apply by emailing recruiter@example.com or calling +1 202-555-0148.",
    )

    request = client.calls[0][1]["json"]
    prompt = request["messages"][1]["content"]
    assert "recruiter@example.com" not in prompt
    assert "+1 202-555-0148" not in prompt
    assert "[redacted-email]" in prompt
    assert "[redacted-phone]" in prompt


def test_extractor_requires_https_endpoint():
    with pytest.raises(ValueError):
        OpenAICompatibleJobRequirementExtractor(
            endpoint="http://matcher.example/chat",
            api_key="key",
            model="model",
        )


def test_environment_returns_none_when_not_configured(monkeypatch):
    for key in (
        "JOBPUNTER_MATCHER_ENDPOINT",
        "JOBPUNTER_MATCHER_API_KEY",
        "JOBPUNTER_MATCHER_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    assert OpenAICompatibleJobRequirementExtractor.from_environment() is None
