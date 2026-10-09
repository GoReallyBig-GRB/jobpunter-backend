from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


class UnusedReviewSession:
    pass


@pytest.mark.parametrize(
    ("auth_provider", "reviewer_ids", "expected_status", "expected_detail"),
    [
        (
            "firebase",
            str(uuid4()),
            403,
            "Requirement review access is not enabled for this user",
        ),
        (
            "test",
            "",
            503,
            "Requirement review requires trusted Firebase authentication outside development",
        ),
    ],
)
def test_requirement_review_queue_rejects_unauthorized_principals(
    monkeypatch, auth_provider, reviewer_ids, expected_status, expected_detail
):
    user_id = uuid4()
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")
    monkeypatch.setenv("JOBPUNTER_REQUIREMENT_REVIEWER_USER_IDS", reviewer_ids)

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user_id, auth_provider=auth_provider
    )

    def override_db():
        yield UnusedReviewSession()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.get("/api/v1/admin/job-requirements")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
