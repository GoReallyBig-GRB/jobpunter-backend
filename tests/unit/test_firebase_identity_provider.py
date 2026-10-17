from datetime import datetime, timezone
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
        self.commit_calls = 0

    def commit(self):
        self.commit_calls += 1

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
        token_verifier=lambda _token: {
            "uid": "new-firebase-uid",
            "name": "New Candidate",
            "email": "New.Candidate@example.com",
            "email_verified": True,
            "picture": "https://example.com/profile.png",
            "auth_time": 1_760_000_000,
        }
    )

    principal = provider.authenticate("Bearer signed-token", db)

    assert principal.user_id is not None
    assert principal.identity_id is not None
    assert principal.auth_provider == "firebase"
    assert db.committed is True
    created_user = next(item for item in db.added if isinstance(item, User))
    created_identity = next(item for item in db.added if isinstance(item, Identity))
    assert created_user.display_name == "New Candidate"
    assert created_user.email == "new.candidate@example.com"
    assert created_user.email_verified is True
    assert created_user.photo_url == "https://example.com/profile.png"
    assert created_user.onboarding_status == "NOT_STARTED"
    assert created_user.last_login_at == datetime.fromtimestamp(1_760_000_000, tz=timezone.utc)
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
    # SQLAlchemy sessions may retain pending objects in the added collection after rollback.


def test_firebase_provider_rejects_non_mapping_verified_claims():
    provider = FirebaseIdentityProvider(token_verifier=lambda _token: ["not", "claims"])

    with pytest.raises(ValueError, match="claims are invalid"):
        provider.authenticate("Bearer signed-token", None)


def test_firebase_verification_initializes_expected_project_and_checks_revocation(monkeypatch):
    import sys

    calls = {}
    app = SimpleNamespace(project_id="jobpunter-project")

    def missing_default_app():
        raise ValueError("default app is not initialized")

    def initialize_app(*, options):
        calls["options"] = options
        return app

    def verify_id_token(token, *, app, check_revoked):
        calls["token"] = token
        calls["app"] = app
        calls["check_revoked"] = check_revoked
        return {"uid": "verified-uid"}

    fake_admin = SimpleNamespace(
        get_app=missing_default_app,
        initialize_app=initialize_app,
        auth=SimpleNamespace(verify_id_token=verify_id_token),
    )
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "jobpunter-project")
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_admin)

    claims = FirebaseIdentityProvider._verify_with_firebase("signed-token")

    assert claims == {"uid": "verified-uid"}
    assert calls["options"] == {"projectId": "jobpunter-project"}
    assert calls["token"] == "signed-token"
    assert calls["app"] is app
    assert calls["check_revoked"] is True



def test_existing_active_user_claims_sync_and_last_login_is_sign_in_time():
    user_id = uuid4()
    identity_id = uuid4()
    identity = SimpleNamespace(id=identity_id, user_id=user_id)
    old_login = datetime.fromtimestamp(1_760_000_000, tz=timezone.utc)
    user = SimpleNamespace(
        id=user_id,
        status="ACTIVE",
        display_name="Old Name",
        email="old@example.com",
        email_verified=False,
        photo_url=None,
        last_login_at=old_login,
    )
    db = FakeSession(identity=identity, user=user)
    provider = FirebaseIdentityProvider(
        token_verifier=lambda _token: {
            "uid": "verified-uid",
            "name": "New Name",
            "email": "New.Name@example.com",
            "email_verified": True,
            "picture": "https://example.com/new.png",
            "auth_time": 1_760_000_100,
        }
    )

    principal = provider.authenticate("Bearer signed-token", db)

    assert principal.user_id == user_id
    assert user.display_name == "New Name"
    assert user.email == "new.name@example.com"
    assert user.email_verified is True
    assert user.photo_url == "https://example.com/new.png"
    assert user.last_login_at == datetime.fromtimestamp(1_760_000_100, tz=timezone.utc)
    assert db.commit_calls == 1


def test_missing_account_claims_do_not_clear_saved_metadata_or_commit():
    user_id = uuid4()
    identity = SimpleNamespace(id=uuid4(), user_id=user_id)
    old_login = datetime.fromtimestamp(1_760_000_000, tz=timezone.utc)
    user = SimpleNamespace(
        id=user_id,
        status="ACTIVE",
        display_name="Saved Name",
        email="saved@example.com",
        email_verified=True,
        photo_url="https://example.com/saved.png",
        last_login_at=old_login,
    )
    db = FakeSession(identity=identity, user=user)
    provider = FirebaseIdentityProvider(token_verifier=lambda _token: {"uid": "verified-uid"})

    provider.authenticate("Bearer signed-token", db)

    assert user.display_name == "Saved Name"
    assert user.email == "saved@example.com"
    assert user.email_verified is True
    assert user.photo_url == "https://example.com/saved.png"
    assert user.last_login_at == old_login
    assert db.commit_calls == 0


def test_last_login_does_not_move_backwards_for_an_older_auth_time():
    user_id = uuid4()
    identity = SimpleNamespace(id=uuid4(), user_id=user_id)
    latest_login = datetime.fromtimestamp(1_760_000_100, tz=timezone.utc)
    user = SimpleNamespace(
        id=user_id,
        status="ACTIVE",
        display_name=None,
        email=None,
        email_verified=False,
        photo_url=None,
        last_login_at=latest_login,
    )
    db = FakeSession(identity=identity, user=user)
    provider = FirebaseIdentityProvider(
        token_verifier=lambda _token: {"uid": "verified-uid", "auth_time": 1_760_000_000}
    )

    provider.authenticate("Bearer signed-token", db)

    assert user.last_login_at == latest_login
    assert db.commit_calls == 0


@pytest.mark.parametrize(
    "claims",
    [
        {"name": "   "},
        {"name": "x" * 201},
        {"email": "not-an-email"},
        {"email": "candidate @example.com"},
        {"picture": "http://example.com/profile.png"},
        {"picture": "x" * 2049},
        {"email_verified": "true"},
        {"auth_time": True},
        {"auth_time": 0},
        {"auth_time": 10**100},
    ],
)
def test_account_claim_parser_ignores_malformed_optional_claims(claims):
    """Malformed optional provider claims must not populate trusted account fields."""
    assert FirebaseIdentityProvider._account_claims(claims) == {}


def test_explicit_false_email_verification_claim_updates_existing_account():
    user_id = uuid4()
    identity = SimpleNamespace(id=uuid4(), user_id=user_id)
    user = SimpleNamespace(
        id=user_id,
        status="ACTIVE",
        display_name="Candidate",
        email="candidate@example.com",
        email_verified=True,
        photo_url=None,
        last_login_at=None,
    )
    db = FakeSession(identity=identity, user=user)
    provider = FirebaseIdentityProvider(
        token_verifier=lambda _token: {"uid": "verified-uid", "email_verified": False}
    )

    provider.authenticate("Bearer signed-token", db)

    assert user.email_verified is False
    assert db.commit_calls == 1

