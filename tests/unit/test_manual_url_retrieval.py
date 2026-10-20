import json
import socket

import pytest

from app.application.jobs.manual_url_retrieval import (
    JobUrlRetrievalError,
    _extract_posting,
    _public_addresses,
    retrieve_job_posting,
)


def test_extracts_jobposting_json_ld_without_inventing_fields():
    body = b"""<!doctype html><html><head>
    <title>Role page</title>
    <script type="application/ld+json">{
      "@context":"https://schema.org",
      "@type":"JobPosting",
      "title":"Technical Product Manager",
      "hiringOrganization":{"@type":"Organization","name":"Example Systems"},
      "description":"Responsibilities include roadmap planning, delivery coordination, stakeholder communication, analyzing product performance, and improving cross-functional workflows. Qualifications include several years of product management experience, strong communication skills, technical fluency, and the ability to analyze customer needs and prioritize delivery.",
      "jobLocation":{"address":{"addressLocality":"Lagos","addressCountry":"Nigeria"}},
      "applicationUrl":"https://jobs.example.test/apply/123"
    }</script></head><body></body></html>"""
    fields = _extract_posting(body, "text/html; charset=utf-8")
    assert fields["title"] == "Technical Product Manager"
    assert fields["company"] == "Example Systems"
    assert "roadmap planning" in fields["description"]
    assert fields["location"] == "Lagos, Nigeria"
    assert fields["application_url"] == "https://jobs.example.test/apply/123"


def test_rejects_unsupported_content_type():
    with pytest.raises(JobUrlRetrievalError) as error:
        _extract_posting(b"not a posting", "application/octet-stream")
    assert error.value.category == "UNSUPPORTED_CONTENT_TYPE"


def test_rejects_private_or_reserved_dns_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ],
    )
    with pytest.raises(JobUrlRetrievalError) as error:
        _public_addresses("internal.example", 80)
    assert error.value.category == "NON_PUBLIC_ADDRESS"


def test_rejects_mixed_public_and_private_dns_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ],
    )
    with pytest.raises(JobUrlRetrievalError) as error:
        _public_addresses("mixed.example", 443)
    assert error.value.category == "NON_PUBLIC_ADDRESS"


def test_redirects_are_bounded_and_https_downgrade_is_blocked(monkeypatch):
    monkeypatch.setattr(
        "app.application.jobs.manual_url_retrieval._fetch_once",
        lambda url: (302, {"location": "http://public.example/job"}, b"")
    )
    with pytest.raises(JobUrlRetrievalError) as error:
        retrieve_job_posting("https://public.example/job")
    assert error.value.category == "HTTPS_DOWNGRADE_BLOCKED"


def test_retrieval_records_final_url_and_time_without_returning_raw_html(monkeypatch):
    html = b"""<html><head><script type="application/ld+json">{
      "@type":"JobPosting","title":"Product Manager",
      "hiringOrganization":{"name":"Example Co"},
      "description":"Responsibilities include planning product strategy, coordinating engineering delivery, defining requirements, managing stakeholders, analyzing product performance, and improving customer workflows. Qualifications include relevant product management experience, strong communication skills, technical fluency, and the ability to prioritize work against customer needs."
    }</script></head></html>"""
    monkeypatch.setattr(
        "app.application.jobs.manual_url_retrieval._fetch_once",
        lambda url: (200, {"content-type": "text/html; charset=utf-8"}, html)
    )
    result = retrieve_job_posting("https://public.example/jobs/1")
    assert result["title"] == "Product Manager"
    assert result["retrieval_final_url"] == "https://public.example/jobs/1"
    assert result["retrieval_method"] == "PUBLIC_HTTP_JOB_POSTING"
    assert result["retrieved_at"]
    assert "raw_html" not in result



def test_rejects_nonstandard_ports_before_network_access(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DNS must not run")),
    )
    from app.application.jobs.manual_url_retrieval import _fetch_once
    with pytest.raises(JobUrlRetrievalError) as error:
        _fetch_once("https://public.example:8443/jobs/1")
    assert error.value.category == "NON_STANDARD_PORT_NOT_ALLOWED"


def test_plain_text_must_contain_substantive_job_content():
    with pytest.raises(JobUrlRetrievalError) as error:
        _extract_posting(b"Product Manager at Example Company", "text/plain")
    assert error.value.category == "POSTING_CONTENT_INCOMPLETE"



def test_redirect_target_is_revalidated_and_private_destination_is_blocked(monkeypatch):
    from app.application.jobs import manual_url_retrieval as retrieval

    original_fetch = retrieval._fetch_once

    def fetch(url):
        if url == "https://public.example/start":
            return 302, {"location": "https://private.example/admin"}, b""
        return original_fetch(url)

    def resolve(hostname, port, **kwargs):
        address = "127.0.0.1" if hostname == "private.example" else "93.184.216.34"
        family = socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(retrieval, "_fetch_once", fetch)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("private redirect target must be rejected before connect"),
    )

    with pytest.raises(JobUrlRetrievalError) as error:
        retrieve_job_posting("https://public.example/start")

    assert error.value.category == "NON_PUBLIC_ADDRESS"


def test_redirect_without_location_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "app.application.jobs.manual_url_retrieval._fetch_once",
        lambda _url: (302, {}, b""),
    )

    with pytest.raises(JobUrlRetrievalError) as error:
        retrieve_job_posting("https://public.example/start")

    assert error.value.category == "REDIRECT_WITHOUT_LOCATION"


def test_redirect_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(
        "app.application.jobs.manual_url_retrieval._fetch_once",
        lambda url: (302, {"location": url}, b""),
    )

    with pytest.raises(JobUrlRetrievalError) as error:
        retrieve_job_posting("https://public.example/start")

    assert error.value.category == "TOO_MANY_REDIRECTS"


def test_manual_url_retrieval_rejects_globally_forbidden_target_before_fetch(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.application.jobs.manual_url_retrieval._fetch_once",
        lambda url: calls.append(url) or (200, {}, b""),
    )

    with pytest.raises(JobUrlRetrievalError) as error:
        retrieve_job_posting(
            "https://blocked.example/jobs/1",
            forbidden_target_rules=[{
                "id": "deny-host",
                "rule_type": "HOST_EXACT",
                "pattern": "blocked.example",
                "connector_scope": "ALL",
                "reason": "Blocked by global target policy",
                "active": True,
            }],
        )

    assert error.value.category == "FORBIDDEN_TARGET_RULE_MATCHED"
    assert calls == []


def test_manual_url_retrieval_rechecks_global_deny_rules_before_redirect_fetch(monkeypatch):
    from app.application.jobs import manual_url_retrieval as retrieval

    start_url = "https://public.example/start"
    blocked_url = "https://blocked.example/private"
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url == start_url:
            return 302, {"location": blocked_url}, b""
        pytest.fail("forbidden redirect target must not be fetched")

    monkeypatch.setattr(retrieval, "_fetch_once", fake_fetch)
    with pytest.raises(JobUrlRetrievalError) as error:
        retrieve_job_posting(
            start_url,
            forbidden_target_rules=[{
                "id": "deny-redirect-host",
                "rule_type": "HOST_EXACT",
                "pattern": "blocked.example",
                "connector_scope": "ALL",
                "reason": "Blocked by global target policy",
                "active": True,
            }],
        )

    assert error.value.category == "FORBIDDEN_TARGET_RULE_MATCHED"
    assert calls == [start_url]
