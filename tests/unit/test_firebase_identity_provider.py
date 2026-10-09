from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.infrastructure.identity.firebase import FirebaseIdentityProvider


class FakeSession:
    def __init__(self, identity=None, user=None):
        self.identity = identity
        self.user = user
        self.scalar_calls = 0
        self.get_calls = []

    def scalar(self, _statement):
        self.scalar_calls += 1
        return self.identity

    def get(self, model, key):
        self.get_calls.append((model, key))
        return self.user


def test_verified_firebase_uid_maps_to_existing_active_application_user():
    user_id = uuid4()
    identity_id = uuid4()
    identity = SimpleNamespace(id=identity_id, user_id=user_id)
    user = SimpleNamespace(id=user_id, status="ACTIVE")
    db = FakeSession(identity=identity, user=user)
    provider = FirebaseIdentityProvider(token_verifier=lambda token: {"uid": "verified-uid"})

    principal = provider.authenticate("Bearer signed-token", db)

    assert principal.user_id == user_id
    assert principal.identity_id == identity_id
    assert principal.auth_provider == "firebase"
    assert db.scalar_calls == 1


def test_verified_firebase_uid_cannot_access_inactive_application_user():
    user_id = uuid4()
    identity = SimpleNamespace(id=uuid4(), user_id=user_id)
    db = FakeSession(identity=identity, user=SimpleNamespace(id=user_id, status="DISABLED"))
    provider = FirebaseIdentityProvider(token_verifier=lambda token: {"uid": "verified-uid"})

    with pytest.raises(ValueError, match="not active"):
        provider.authenticate("Bearer signed-token", db)


@pytest.mark.parametrize("header", ["", "token", "Basic abc", "Bearer "])
def test_firebase_provider_rejects_malformed_authorization_header(header):
    provider = FirebaseIdentityProvider(token_verifier=lambda token: pytest.fail("verifier must not run"))

    with pytest.raises(ValueError, match="Bearer token required"):
        provider.authenticate(header, None)


def test_firebase_provider_does_not_accept_blank_uid_claim():
    provider = FirebaseIdentityProvider(token_verifier=lambda token: {"uid": "  "})

    with pytest.raises(ValueError, match="UID"):
        provider.authenticate("Bearer signed-token", None)


def test_firebase_verification_requires_project_id(monkeypatch):
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    with pytest.raises(RuntimeError, match="FIREBASE_PROJECT_ID"):
        FirebaseIdentityProvider._verify_with_firebase("token")


def test_firebase_verification_rejects_default_app_for_wrong_project(monkeypatch):
    import sys

    fake_admin = SimpleNamespace(
        get_app=lambda: SimpleNamespace(project_id="wrong-project"),
        initialize_app=lambda **kwargs: pytest.fail("must not initialize a second app"),
        auth=SimpleNamespace(verify_id_token=lambda *args, **kwargs: pytest.fail("must not verify")),
    )
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "jobpunter-project")
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_admin)

    with pytest.raises(RuntimeError, match="does not match"):
        FirebaseIdentityProvider._verify_with_firebase("token")


@pytest.mark.parametrize("failure", [Exception("expired token"), Exception("revoked token")])
def test_firebase_verification_rejects_invalid_or_revoked_tokens(monkeypatch, failure):
    import sys

    fake_admin = SimpleNamespace(
        get_app=lambda: SimpleNamespace(project_id="jobpunter-project"),
        auth=SimpleNamespace(verify_id_token=lambda *args, **kwargs: (_ for _ in ()).throw(failure)),
    )
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "jobpunter-project")
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_admin)

    with pytest.raises(ValueError, match="invalid, expired, or revoked"):
        FirebaseIdentityProvider._verify_with_firebase("token")
