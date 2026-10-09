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


class ProvisioningSession:
    def __init__(self, existing_identity=None, existing_user=None, race_identity=None, race_user=None):
        self.identity = existing_identity
        self.user = existing_user
        self.race_identity = race_identity
        self.race_user = race_user
        self.added = []
        self.committed = False
        self.rolled_back = False

    def scalar(self, _statement):
        return self.identity

    def get(self, model, key):
        return self.user

    def add(self, instance):
        self.added.append(instance)

    def flush(self):
        for instance in self.added:
            if instance.__class__.__name__ == "User" and getattr(instance, "id", None) is None:
                instance.id = uuid4()

    def commit(self):
        from sqlalchemy.exc import IntegrityError

        if self.race_identity is not None and not self.rolled_back:
            raise IntegrityError("insert identity", {}, Exception("unique constraint"))
        self.committed = True
        for instance in self.added:
            if instance.__class__.__name__ == "Identity":
                instance.id = uuid4()
                self.identity = instance
                self.user = next(
                    item for item in self.added if item.__class__.__name__ == "User"
                )

    def refresh(self, _instance):
        return None

    def rollback(self):
        self.rolled_back = True
        self.identity = self.race_identity
        self.user = self.race_user


def test_first_firebase_sign_in_provisions_local_user_and_identity():
    from app.infrastructure.db.models.identity import Identity, User

    db = ProvisioningSession()
    provider = FirebaseIdentityProvider(
        token_verifier=lambda _token: {"uid": "new-firebase-uid", "name": "New Candidate"}
    )

    principal = provider.authenticate("Bearer signed-token", db)

    assert principal.user_id is not None
    assert principal.identity_id is not None
    assert principal.auth_provider == "firebase"
    assert db.committed is True
    created_user = next(item for item in db.added if isinstance(item, User))
    created_identity = next(item for item in db.added if isinstance(item, Identity))
    assert created_user.display_name == "New Candidate"
    assert created_user.status == "ACTIVE"
    assert created_identity.user_id == created_user.id
    assert created_identity.provider == "FIREBASE"
    assert created_identity.provider_subject == "new-firebase-uid"


def test_first_sign_in_unique_identity_race_reloads_existing_identity():
    from app.infrastructure.db.models.identity import Identity, User

    existing_user_id = uuid4()
    existing_identity = SimpleNamespace(id=uuid4(), user_id=existing_user_id)
    existing_user = SimpleNamespace(id=existing_user_id, status="ACTIVE")
    db = ProvisioningSession(
        race_identity=existing_identity,
        race_user=existing_user,
    )
    provider = FirebaseIdentityProvider(token_verifier=lambda _token: {"uid": "racing-uid"})

    principal = provider.authenticate("Bearer signed-token", db)

    assert principal.user_id == existing_user_id
    assert principal.identity_id == existing_identity.id
    assert db.rolled_back is True
    assert db.committed is False
    assert not any(isinstance(item, Identity) for item in db.added)
