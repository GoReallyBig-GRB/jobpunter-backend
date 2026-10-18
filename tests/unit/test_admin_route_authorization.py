import inspect
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fastapi.routing import APIRoute

from app.api.deps import get_principal
from app.api.v1 import admin as admin_api
from app.api.v1 import support as support_api
import app.api.v1.router as api_router_module
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


def _defined_api_routes():
    """Inspect routes on their defining routers, independent of FastAPI include wrappers."""
    routers = [
        api_router_module.candidate_router,
        api_router_module.opportunities_router,
        api_router_module.application_packages_router,
        api_router_module.document_imports_router,
        api_router_module.documents_router,
        api_router_module.cv_studio_router,
        api_router_module.discovery_router,
        api_router_module.preferences_router,
        api_router_module.career_pathways_router,
        api_router_module.requirement_review_router,
        api_router_module.notifications_router,
        api_router_module.candidate_support_router,
        api_router_module.admin_support_router,
        api_router_module.ai_router,
        api_router_module.email_parser_router,
        api_router_module.manual_ingestion_router,
        api_router_module.job_pool_router,
        api_router_module.admin_router,
    ]
    return [
        (router, route)
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute)
    ]


class NoAdminGrantSession:
    def scalar(self, _statement):
        return None


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/ai-executions",
        "/api/v1/admin/audit",
        "/api/v1/admin/configs",
        "/api/v1/admin/support/tickets",
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

@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "post",
            "/api/v1/admin/configs",
            {"config_key": "ai.task.cv_generation", "payload": {"model": "gemini-test"}},
        ),
        (
            "post",
            f"/api/v1/admin/configs/{uuid4()}/publish",
            None,
        ),
        (
            "post",
            f"/api/v1/admin/configs/{uuid4()}/rollback",
            None,
        ),
        (
            "patch",
            f"/api/v1/admin/support/tickets/{uuid4()}/assignment",
            {"admin_user_id": str(uuid4())},
        ),
        (
            "patch",
            f"/api/v1/admin/users/{uuid4()}/status",
            {
                "target_status": "SUSPENDED",
                "expected_revision": 1,
                "reason": "Security support request",
            },
        ),
    ],
)
def test_governance_mutation_routes_deny_users_without_admin_grants(method, path, payload):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=uuid4(), auth_provider="firebase"
    )

    def override_db():
        yield NoAdminGrantSession()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 403
    assert response.json()["detail"] == "Administrator permission required."

def test_every_admin_governance_and_support_route_calls_the_admin_guard():
    """Keep the complete admin route surface behind the shared grant check."""
    guarded_modules = {admin_api.__name__, support_api.__name__}
    routes = [
        (router, route)
        for router, route in _defined_api_routes()
        if (
            router is admin_api.router
            or router is support_api.candidate_router
            or router is support_api.admin_router
        )
        and route.endpoint.__module__ in guarded_modules
    ]

    assert routes, "Expected registered Admin governance/support routes."
    missing_guards = [
        f"{','.join(sorted(route.methods or []))} /api/v1{router.prefix}{route.path} -> {route.endpoint.__name__}"
        for router, route in routes
        if "_require_admin(" not in inspect.getsource(route.endpoint)
    ]

    assert not missing_guards, (
        "Admin governance/support handlers must include _require_admin: "
        + "; ".join(missing_guards)
    )

def test_every_candidate_and_admin_route_requires_authenticated_principal():
    """Prevent private API routes from accidentally losing the identity dependency."""
    private_routes = [
        (router, route)
        for router, route in _defined_api_routes()
        if ("/api/v1" + router.prefix + route.path).startswith(
            ("/api/v1/candidate/", "/api/v1/admin/")
        )
    ]

    assert private_routes, "Expected registered candidate and Admin API routes."
    missing_auth = [
        f"{','.join(sorted(route.methods or []))} /api/v1{router.prefix}{route.path}"
        for router, route in private_routes
        if not any(
            dependency.call is get_principal
            for dependency in route.dependant.dependencies
        )
    ]

    assert not missing_auth, (
        "Candidate/Admin routes must depend on get_principal: "
        + "; ".join(missing_auth)
    )
