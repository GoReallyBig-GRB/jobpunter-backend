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
                "https://acme.example/jobs/1\n" + "Responsibilities and requirements. " * 4,
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
