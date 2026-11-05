from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.infrastructure.acquisition.public_http_jsonld import (
    PublicHttpJsonLdConnector,
    PublicHttpJsonLdError,
)
from app.ports.acquisition import AcquisitionOutcome


class FakeResponse:
    def __init__(self, body: bytes, *, content_type: str = "text/html") -> None:
        self.body = body
        self.headers = {"content-type": content_type}
        self.history = []

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=64 * 1024):
        yield self.body


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = []

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        yield self.response


def _html(script: str) -> bytes:
    return ('<html><script type="application/ld+json">' + script + "</script></html>").encode()


def test_public_http_connector_extracts_jobposting_json_ld(monkeypatch):
    client = FakeClient(
        FakeResponse(
            _html(
                '{"@context":"https://schema.org","@graph":[{'
                '"@type":"JobPosting","identifier":{"value":"job-1"},'
                '"title":"Product Manager","hiringOrganization":{"name":"Acme"},'
                '"jobLocation":{"address":{"addressLocality":"Lagos",'
                '"addressCountry":"NG"}},"description":"Build products",'
                '"datePosted":"2026-11-05","url":"https://jobs.example.org/job-1"}]}'
            )
        )
    )
    observed = []
    connector = PublicHttpJsonLdConnector(http_client=client, allow_unsafe_http_client=True)
    result = connector.acquire(
        source_id=uuid4(),
        context={
            "page_url": "https://jobs.example.org/job-1",
            "acquisition_policy_revalidator": lambda **kwargs: observed.append(kwargs),
        },
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert len(result.records) == 1
    record = result.records[0]
    assert record.external_record_id == "job-1"
    assert record.source_url == "https://jobs.example.org/job-1"
    assert record.raw_content["jobpunter_normalized"]["company"] == "Acme"
    assert record.raw_content["jobpunter_normalized"]["location"] == "Lagos, NG"
    assert record.raw_content["application_url"] is None
    assert observed == [
        {
            "url": "https://jobs.example.org/job-1",
            "connector_type": "PUBLIC_HTTP",
            "method": "GET",
        }
    ]
    assert client.calls[0][2]["follow_redirects"] is False


def test_public_http_connector_blocks_forbidden_target_before_network():
    client = FakeClient(FakeResponse(b"<html />"))
    connector = PublicHttpJsonLdConnector(http_client=client, allow_unsafe_http_client=True)
    with pytest.raises(PublicHttpJsonLdError) as exc_info:
        connector.acquire(
            source_id=uuid4(),
            context={
                "page_url": "https://jobs.example.org/job-1",
                "forbidden_target_rules": [
                    {
                        "id": "rule-1",
                        "rule_type": "HOST_EXACT",
                        "pattern": "jobs.example.org",
                        "connector_scope": "PUBLIC_HTTP",
                        "reason": "blocked",
                        "active": True,
                    }
                ],
            },
        )
    assert exc_info.value.status == AcquisitionOutcome.RESTRICTED
    assert exc_info.value.error_category == "FORBIDDEN_TARGET_RULE_MATCHED"
    assert client.calls == []


def test_public_http_connector_rejects_malformed_json_ld():
    connector = PublicHttpJsonLdConnector(
        http_client=FakeClient(FakeResponse(_html("{not-json"))),
        allow_unsafe_http_client=True,
    )
    with pytest.raises(PublicHttpJsonLdError) as exc_info:
        connector.acquire(source_id=uuid4(), context={"page_url": "https://jobs.example.org/job-1"})
    assert exc_info.value.error_category == "PUBLIC_HTTP_INVALID_JSON_LD"


def test_public_http_connector_returns_truthful_empty_success_without_json_ld():
    connector = PublicHttpJsonLdConnector(
        http_client=FakeClient(FakeResponse(b"<html><body>No structured job</body></html>")),
        allow_unsafe_http_client=True,
    )
    result = connector.acquire(
        source_id=uuid4(), context={"page_url": "https://jobs.example.org/job-1"}
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records == ()


def test_public_http_connector_does_not_trust_off_domain_json_ld_url():
    connector = PublicHttpJsonLdConnector(
        http_client=FakeClient(
            FakeResponse(
                _html('{"@type":"JobPosting","title":"PM","url":"https://attacker.example/forged"}')
            )
        ),
        allow_unsafe_http_client=True,
    )
    result = connector.acquire(
        source_id=uuid4(), context={"page_url": "https://jobs.example.org/job-1"}
    )
    assert result.records[0].source_url == "https://jobs.example.org/job-1"
