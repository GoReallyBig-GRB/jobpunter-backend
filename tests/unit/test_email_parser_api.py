from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.deps import get_principal
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


def test_authenticated_email_parser_route_returns_structured_job_alert():
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=uuid4(), auth_provider="test"
    )
    try:
        response = TestClient(app).post(
            "/api/v1/email/parse-job-alert",
            json={
                "subject": "Product Manager at Acme",
                "sender": "alerts@linkedin.com",
                "content": "Title: Product Manager\nCompany: Acme\nLocation: Remote\n"
                "https://acme.example/jobs/1\n" + "Responsibilities include managing product delivery, coordinating stakeholders, and improving workflows. Qualifications require product management experience, communication skills, and technical fluency. " * 3,
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["detectedSource"] == "LinkedIn"
    assert payload["parsingStatus"] == "PROCESSED"
    assert payload["extractedJobs"][0]["title"] == "Product Manager"
    assert payload["extractedJobs"][0]["company"] == "Acme"


def test_email_parser_route_rejects_unknown_request_fields():
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=uuid4(), auth_provider="test"
    )
    try:
        response = TestClient(app).post(
            "/api/v1/email/parse-job-alert",
            json={"subject": "Job alert", "content": "Body", "provider": "client-controlled"},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)

    assert response.status_code == 422



def test_email_parser_uses_stable_message_identity_for_replays():
    from app.application.jobs.email_parser import parse_job_alert_email

    first = parse_job_alert_email(
        "Job alert",
        "Title: Product Manager\nCompany: Acme\n" + "Responsibilities include managing product delivery, coordinating stakeholders, and improving workflows. Qualifications require product management experience, communication skills, and technical fluency. " * 3,
        "alerts@example.com",
        source_message_id="provider-message-123",
    )
    replay = parse_job_alert_email(
        "Job alert",
        "Title: Product Manager\nCompany: Acme\n" + "Responsibilities include managing product delivery, coordinating stakeholders, and improving workflows. Qualifications require product management experience, communication skills, and technical fluency. " * 3,
        "alerts@example.com",
        source_message_id="provider-message-123",
    )
    assert first["sourceEmail"]["id"] == replay["sourceEmail"]["id"]
    assert first["extractedJobs"][0]["sourceMessageId"] == "provider-message-123"


def test_email_parser_returns_confident_jobs_and_ambiguous_chunks_separately():
    from app.application.jobs.email_parser import parse_job_alert_email

    digest = (
        "###\nTitle: Product Manager\nCompany: Acme\n"
        + "Responsibilities include managing product delivery, coordinating stakeholders, and improving workflows. Qualifications require product management experience, communication skills, and technical fluency. " * 3
        + "\nhttps://acme.example/jobs/1\n"
        + "###\nThis chunk does not identify a role or company. "
        + "Additional generic notification text. " * 3
    )
    parsed = parse_job_alert_email("2 new jobs", digest, "alerts@example.com", "msg-2")
    assert parsed["parsingStatus"] == "PARTIALLY_PROCESSED"
    assert len(parsed["extractedJobs"]) == 1
    assert parsed["reviewRequiredCount"] == 1
    assert "content" in parsed["reviewRequiredChunks"][0]


def test_email_parser_does_not_emit_incomplete_single_job_as_extracted():
    from app.application.jobs.email_parser import parse_job_alert_email

    parsed = parse_job_alert_email("Job alert", "Title: Product Manager\nResponsibilities only.", "alerts@example.com")

    assert parsed["parsingStatus"] == "PARSING_REQUIRED"
    assert parsed["extractedJobs"] == []
    assert parsed["reviewRequiredCount"] == 1
    assert parsed["reviewRequiredChunks"][0]["content"]


def test_email_parser_api_accepts_source_message_id():
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=uuid4(), auth_provider="test"
    )
    try:
        response = TestClient(app).post(
            "/api/v1/email/parse-job-alert",
            json={
                "subject": "Product Manager at Acme",
                "sender": "alerts@example.com",
                "source_message_id": "provider-message-456",
                "content": "Title: Product Manager\nCompany: Acme\n" + "Responsibilities include managing product delivery, coordinating stakeholders, and improving workflows. Qualifications require product management experience, communication skills, and technical fluency. " * 3,
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)

    assert response.status_code == 200, response.text
    assert response.json()["sourceEmail"]["sourceMessageId"] == "provider-message-456"

def test_email_parser_splits_repeated_title_fields_without_visual_separators():
    from app.application.jobs.email_parser import parse_job_alert_email

    body = (
        "Title: Product Manager\nCompany: Acme\n"
        + "Responsibilities include managing product delivery, coordinating stakeholders, and improving workflows. " * 2
        + "Qualifications require product management experience, communication skills, and technical fluency. "
        + "\n\nTitle: Technical Product Owner\nCompany: Beta\n"
        + "Responsibilities include owning technical roadmaps, coordinating engineering, and improving platform reliability. " * 2
        + "Qualifications include technical product ownership experience, communication skills, and the ability to lead cross-functional delivery. "
    )
    parsed = parse_job_alert_email("2 new jobs", body, "alerts@example.com", "digest-no-separators")

    assert parsed["parsingStatus"] == "PROCESSED"
    assert [job["title"] for job in parsed["extractedJobs"]] == [
        "Product Manager", "Technical Product Owner",
    ]
    assert [job["company"] for job in parsed["extractedJobs"]] == ["Acme", "Beta"]
    assert parsed["reviewRequiredChunks"] == []


def test_email_parser_keeps_a_single_job_with_multiple_paragraphs_together():
    from app.application.jobs.email_parser import parse_job_alert_email

    body = (
        "Title: Product Manager\nCompany: Acme\n\n"
        "Responsibilities include managing product delivery, coordinating stakeholders, defining requirements, prioritizing roadmaps, and improving workflows.\n\n"
        "Qualifications require product management experience, strong communication skills, technical fluency, and the ability to analyze customer needs. "
        "The role also requires leading cross-functional planning and ensuring measurable delivery outcomes."
    )
    parsed = parse_job_alert_email("Product Manager at Acme", body, "alerts@example.com")

    assert parsed["parsingStatus"] == "PROCESSED"
    assert len(parsed["extractedJobs"]) == 1
    assert "Qualifications require" in parsed["extractedJobs"][0]["description"]

def test_email_parser_detects_separator_delimited_digest_without_subject_count_or_headings():
    from app.application.jobs.email_parser import parse_job_alert_email

    body = (
        "Product Manager\nAcme\n"
        + "Responsibilities include managing product delivery, coordinating stakeholders, and improving workflows. " * 2
        + "Qualifications require product management experience, communication skills, and technical fluency. "
        + "The role requires clear written communication and cross-functional collaboration. "
        + "\n---\n"
        + "Technical Product Owner\nBeta\n"
        + "Responsibilities include owning technical roadmaps, coordinating engineering, and improving platform reliability. " * 2
        + "Qualifications include technical product ownership experience, communication skills, and cross-functional delivery. "
    )
    parsed = parse_job_alert_email("Your opportunities", body, "alerts@example.com", "separator-only-digest")

    assert parsed["isMultiJobAlert"] is True
    assert parsed["parsingStatus"] == "PROCESSED"
    assert [job["title"] for job in parsed["extractedJobs"]] == [
        "Product Manager",
        "Technical Product Owner",
    ]
    assert [job["company"] for job in parsed["extractedJobs"]] == ["Acme", "Beta"]
