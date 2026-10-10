import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.models import CandidatePreferences, CandidateProfile, User
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


@pytest.fixture
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def make_user(db):
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()
    return user


def test_preferences_api_persists_versioned_candidate_owned_settings(db):
    owner = make_user(db)
    other_user = make_user(db)
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=owner.id, auth_provider="test"
    )
    connection = db.connection()

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        initial = client.get("/api/v1/candidate/preferences")
        assert initial.status_code == 200
        assert initial.json()["revision"] == 0
        assert initial.json()["preferences"]["maxApplicationFee"] == 0

        updated = client.put(
            "/api/v1/candidate/preferences",
            json={
                "expectedRevision": 0,
                "preferences": {
                    "targetRoles": ["Technical Product Manager"],
                    "excludedJobTypes": ["quota-carrying sales", "SDR"],
                    "preferredLocations": ["Nigeria"],
                    "remotePreference": "REMOTE_PREFERRED",
                    "maxApplicationFee": 0,
                    "transferabilityPolicy": "STRICT",
                },
            },
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == 1
        assert updated.json()["preferences"]["targetRoles"] == ["Technical Product Manager"]
        assert updated.json()["preferences"]["excludedJobTypes"] == ["quota-carrying sales", "SDR"]
        assert updated.json()["preferences"]["remotePreference"] == "REMOTE_PREFERRED"

        persisted = db.scalar(select(CandidatePreferences))
        assert persisted is not None
        profile = db.scalar(
            select(CandidateProfile).where(CandidateProfile.id == persisted.candidate_profile_id)
        )
        assert profile is not None
        assert profile.user_id == owner.id
        assert persisted.preferences["maxApplicationFee"] == 0

        second_update = client.put(
            "/api/v1/candidate/preferences",
            json={
                "expectedRevision": 1,
                "preferences": {
                    "targetRoles": ["Product Operations"],
                    "targetDomains": ["B2B SaaS"],
                    "preferredIndustries": ["Technology"],
                    "workAuthorizations": ["Nigeria"],
                    "salaryExpectation": "USD 80,000-100,000 annually",
                    "notes": "No quota-carrying sales roles.",
                    "terminologyPreferences": {"prefer": ["technical product"], "avoid": ["rockstar"]},
                    "evidenceExclusions": ["Unverified AI-generated claims"],
                    "remotePreference": "HYBRID_OK",
                    "transferabilityPolicy": "BALANCED",
                    "maxApplicationFee": 0,
                },
            },
        )
        assert second_update.status_code == 200
        assert second_update.json()["revision"] == 2
        assert second_update.json()["preferences"]["targetRoles"] == ["Product Operations"]
        round_tripped = second_update.json()["preferences"]
        assert round_tripped["targetDomains"] == ["B2B SaaS"]
        assert round_tripped["preferredIndustries"] == ["Technology"]
        assert round_tripped["workAuthorizations"] == ["Nigeria"]
        assert round_tripped["salaryExpectation"] == "USD 80,000-100,000 annually"
        assert round_tripped["notes"] == "No quota-carrying sales roles."
        assert round_tripped["terminologyPreferences"] == {"prefer": ["technical product"], "avoid": ["rockstar"]}
        assert round_tripped["evidenceExclusions"] == ["Unverified AI-generated claims"]
        assert round_tripped["remotePreference"] == "HYBRID_OK"
        assert round_tripped["transferabilityPolicy"] == "BALANCED"

        # A fresh GET must return the saved database state, not just the PUT response.
        reloaded = client.get("/api/v1/candidate/preferences")
        assert reloaded.status_code == 200
        assert reloaded.json()["revision"] == 2
        assert reloaded.json()["preferences"] == round_tripped

        stale = client.put(
            "/api/v1/candidate/preferences",
            json={"expectedRevision": 0, "preferences": {"targetRoles": ["Product Manager"]}},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
        assert stale.json()["error"]["details"]["current_revision"] == 2

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other_user.id, auth_provider="test"
        )
        other = client.get("/api/v1/candidate/preferences")
        assert other.status_code == 200
        assert other.json()["revision"] == 0
        assert other.json()["preferences"]["targetRoles"] == []
        assert other.json()["preferences"]["excludedJobTypes"] == []

        # The second candidate can only update their own preferences.
        other_update = client.put(
            "/api/v1/candidate/preferences",
            json={
                "expectedRevision": 0,
                "preferences": {"targetRoles": ["Injected into another account"]},
            },
        )
        assert other_update.status_code == 200
        assert other_update.json()["preferences"]["targetRoles"] == ["Injected into another account"]

        # Switching back to the owner must show the owner's unchanged record.
        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=owner.id, auth_provider="test"
        )
        owner_after_other_update = client.get("/api/v1/candidate/preferences")
        assert owner_after_other_update.status_code == 200
        assert owner_after_other_update.json()["revision"] == 2
        assert owner_after_other_update.json()["preferences"]["targetRoles"] == ["Product Operations"]
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_preferences_api_rejects_unknown_fields_and_invalid_values(db):
    user = make_user(db)
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        unknown = client.put(
            "/api/v1/candidate/preferences",
            json={
                "expectedRevision": 0,
                "preferences": {"unrecognizedGlobalOverride": True},
            },
        )
        assert unknown.status_code == 422

        invalid_fee = client.put(
            "/api/v1/candidate/preferences",
            json={"expectedRevision": 0, "preferences": {"maxApplicationFee": -1}},
        )
        assert invalid_fee.status_code == 422

        invalid_salary_range = client.put(
            "/api/v1/candidate/preferences",
            json={
                "expectedRevision": 0,
                "preferences": {"salaryMin": 150000, "salaryMax": 100000},
            },
        )
        assert invalid_salary_range.status_code == 422
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("salaryMin", float("inf")),
        ("salaryMax", float("-inf")),
        ("maxApplicationFee", float("inf")),
    ],
)
def test_preferences_api_rejects_non_finite_money_values(db, field, value):
    user = make_user(db)
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.put(
            "/api/v1/candidate/preferences",
            json={"expectedRevision": 0, "preferences": {field: value}},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
