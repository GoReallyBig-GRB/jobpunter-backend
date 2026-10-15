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
      "description":"Responsibilities include roadmap planning, delivery coordination, stakeholder communication, and product analysis. Qualifications include product management experience and technical fluency.",
      "jobLocation":{"address":{"addressLocality":"Lagos","addressCountry":"Nigeria"}},
      "url":"https://jobs.example.test/apply/123"
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
      "description":"Responsibilities include planning product strategy and coordinating engineering delivery. Qualifications include relevant experience, stakeholder communication, and analytical skills."
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
