"""Identity provisioning must never merge accounts by email alone."""
from __future__ import annotations

from uuid import uuid4

from app.infrastructure.db.models.identity import Identity, User
from app.infrastructure.identity.firebase import FirebaseIdentityProvider


class ProvisioningSession:
    """Persistence double that records identity lookups during first sign-in."""

    def __init__(self, existing_user: User) -> None:
        self.existing_user = existing_user
        self.added: list[object] = []
        self.scalar_queries: list[str] = []
        self.committed = False

    def scalar(self, statement):
        query = str(statement)
        self.scalar_queries.append(query)
        # The first-sign-in path should look up only the provider subject. If
        # production code adds an email-based account lookup, this test fails.
        if "users.email" in query:
            return self.existing_user
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
    existing_user.id = uuid4()
    db = ProvisioningSession(existing_user)
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
    assert len(db.scalar_queries) == 1
    assert "users.email" not in db.scalar_queries[0]
    assert "identities.provider" in db.scalar_queries[0]
    assert "identities.provider_subject" in db.scalar_queries[0]
    assert principal.user_id == created_user.id
    assert principal.user_id != existing_user.id
    assert created_user.email == existing_user.email
    assert created_identity.user_id == created_user.id
    assert created_identity.provider_subject == "new-provider-subject"
