from uuid import uuid4

import pytest

import app.api.deps as auth_deps
from app.ports.identity import AuthenticatedPrincipal


class FakeIdentityProvider:
    def __init__(self, principal):
        self.principal = principal
        self.received = None

    def authenticate(self, authorization_header, db):
        self.received = (authorization_header, db)
        return self.principal


def test_authentication_boundary_accepts_a_provider_implementing_the_port(monkeypatch):
    user_id = uuid4()
    identity_id = uuid4()
    db = object()
    provider = FakeIdentityProvider(AuthenticatedPrincipal(
        user_id=user_id,
        identity_id=identity_id,
        auth_provider="test-provider",
    ))
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "test")
    monkeypatch.delenv("JOBPUNTER_ALLOW_DEV_IDENTITY", raising=False)
    monkeypatch.setattr(auth_deps, "get_identity_provider", lambda: provider)

    principal = auth_deps.get_principal(
        authorization="Bearer test-token",
        x_user_id=None,
        db=db,
    )

    assert principal == provider.principal
    assert principal.auth_provider == "test-provider"
    assert provider.received == ("Bearer test-token", db)


def test_unknown_identity_provider_fails_closed(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_IDENTITY_PROVIDER", "unknown-provider")

    with pytest.raises(RuntimeError, match="Configured identity provider is unsupported"):
        auth_deps.get_identity_provider()
