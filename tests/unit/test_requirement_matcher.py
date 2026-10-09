import json

import httpx
import pytest

from app.infrastructure.ai.requirement_matcher import (
    MatcherConfigurationError,
    MatcherProviderError,
    OpenAICompatibleRequirementMatcher,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "bad response", request=httpx.Request("POST", "https://matcher.example/v1/chat/completions"),
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


def make_matcher(client):
    return OpenAICompatibleRequirementMatcher(
        endpoint="https://matcher.example/v1/chat/completions",
        api_key="test-key",
        model="test-model",
        client=client,
    )


def provider_response(assessments):
    return FakeResponse({
        "choices": [{
            "message": {"content": json.dumps({"assessments": assessments})}
        }]
    })


def test_matcher_returns_explicit_assessments_and_only_known_evidence_ids():
    client = FakeClient(provider_response([{
        "requirement_id": "req-1",
        "result": "MATCH",
        "evidence_ids": ["ev-1"],
        "rationale": "Approved product delivery evidence supports the requirement.",
    }]))
    matcher = make_matcher(client)

    result = matcher(
        [{"id": "req-1", "description": "Product delivery", "extraction_status": "COMPLETE"}],
        [{"id": "claim-1", "status": "APPROVED", "claim_content": {"skill": "product delivery"}}],
        [{"id": "ev-1", "status": "APPROVED", "content": {"summary": "Delivered SaaS product"}}],
        type("Job", (), {"canonical_title": "Product Manager", "company_name": "Example"})(),
    )

    assert result[0]["result"] == "MATCH"
    assert result[0]["evidence_ids"] == ["ev-1"]
    assert client.calls[0][1]["json"]["temperature"] == 0
    assert client.calls[0][1]["headers"]["Authorization"] == "Bearer test-key"


def test_matcher_downgrades_match_without_evidence_to_unknown():
    matcher = make_matcher(FakeClient(provider_response([{
        "requirement_id": "req-1",
        "result": "MATCH",
        "evidence_ids": [],
        "rationale": "Not enough proof.",
    }])))

    result = matcher(
        [{"id": "req-1"}], [], [], type("Job", (), {})()
    )

    assert result[0]["result"] == "UNKNOWN"


@pytest.mark.parametrize(
    "assessment",
    [
        {"requirement_id": "invented", "result": "MATCH", "evidence_ids": ["ev-1"]},
        {"requirement_id": "req-1", "result": "MATCH", "evidence_ids": ["invented"]},
        {"requirement_id": "req-1", "result": "MAYBE", "evidence_ids": []},
    ],
)
def test_matcher_rejects_unknown_ids_or_invalid_result(assessment):
    matcher = make_matcher(FakeClient(provider_response([assessment])))

    with pytest.raises(MatcherProviderError):
        matcher([{"id": "req-1"}], [], [{"id": "ev-1"}], type("Job", (), {})())


def test_matcher_requires_https_endpoint():
    with pytest.raises(MatcherConfigurationError):
        OpenAICompatibleRequirementMatcher(
            endpoint="http://matcher.example/v1",
            api_key="key",
            model="model",
        )


def test_environment_requires_all_matcher_settings(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_MATCHER_ENDPOINT", "https://matcher.example/v1")
    monkeypatch.delenv("JOBPUNTER_MATCHER_API_KEY", raising=False)
    monkeypatch.delenv("JOBPUNTER_MATCHER_MODEL", raising=False)

    with pytest.raises(MatcherConfigurationError):
        OpenAICompatibleRequirementMatcher.from_environment()


def test_environment_returns_none_when_not_configured(monkeypatch):
    for key in (
        "JOBPUNTER_MATCHER_ENDPOINT",
        "JOBPUNTER_MATCHER_API_KEY",
        "JOBPUNTER_MATCHER_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    assert OpenAICompatibleRequirementMatcher.from_environment() is None
