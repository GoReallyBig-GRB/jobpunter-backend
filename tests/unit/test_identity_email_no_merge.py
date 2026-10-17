"""Identity provisioning must never merge accounts by email alone."""
from __future__ import annotations

from uuid import uuid4

from app.infrastructure.db.models.identity import Identity, User
from app.infrastructure.identity.firebase import FirebaseIdentityProvider


class ProvisioningSession:
    """Minimal persistence double for the first-sign-in provisioning path."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = False

    def scalar(self, _statement):
        # No identity exists for the verified Firebase provider subject.
        return None

    def add(self, instance) -> None:
        self.added.append(instance)

    def flush(self) -> None:
        for instance in self.added:
            if isinstance(instance, User) and instance.id is None:
                instance.id = uuid4()

    def commit(self) -> None:
        self.committed = True
        for instance in self.added:
            if isinstance(instance, Identity) and instance.id is None:
                instance.id = uuid4()

    def refresh(self, _instance) -> None:
        return None


def test_first_sign_in_does_not_merge_existing_account_by_matching_email() -> None:
    existing_user = User(
        display_name="Existing account",
        email="candidate@example.com",
        status="ACTIVE",
    )
    db = ProvisioningSession()
    provider = FirebaseIdentityProvider(
        token_verifier=lambda _token: {
            "uid": "new-provider-subject",
            "email": "candidate@example.com",
            "email_verified": True,
        }
    )

    principal = provider.authenticate("Bearer verified-token", db)

    created_user = next(item for item in db.added if isinstance(item, User))
    created_identity = next(item for item in db.added if isinstance(item, Identity))

    assert db.committed is True
    assert principal.user_id == created_user.id
    assert principal.user_id != existing_user.id
    assert created_user.email == existing_user.email
    assert created_identity.user_id == created_user.id
    assert created_identity.provider_subject == "new-provider-subject"
