from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


class NoAdminGrantSession:
    def scalar(self, _statement):
        return None


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/ai-executions",
        "/api/v1/admin/audit",
        "/api/v1/admin/configs",
    ],
)
def test_governance_read_routes_deny_authenticated_users_without_admin_grants(path):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=uuid4(), auth_provider="firebase"
    )

    def override_db():
        yield NoAdminGrantSession()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.get(path)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 403
    assert response.json()["detail"] == "Administrator permission required."
