import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.main import app
from app.application.notifications.service import (
    NotificationService,
    application_status_notification_handler,
    punt_notification_handler,
)
from app.infrastructure.db.models import (
    Application,
    ApplicationEvent,
    CandidateProfile,
    Job,
    Notification,
    Punt,
    User,
)
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


def make_candidate(db):
    user = User(display_name=f"Notification candidate {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return user, profile


def make_job(db, title="Technical Product Manager"):
    job = Job(
        canonical_title=title,
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="FRESH",
        application_url="https://example.test/apply",
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    return job


def handler_factory(db):
    return sessionmaker(
        bind=db.connection(),
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )


def test_punt_notification_is_created_from_persisted_punt_and_is_idempotent(db):
    user, profile = make_candidate(db)
    job = make_job(db)
    now = datetime.now(timezone.utc)
    punt = Punt(
        candidate_profile_id=profile.id,
        job_id=job.id,
        status="ACTIVE",
        qualified_at=now,
    )
    db.add(punt)
    db.flush()
    punt_id = punt.id
    handle = punt_notification_handler(handler_factory(db))

    handle({"punt_id": str(punt_id)})
    handle({"punt_id": str(punt_id)})

    rows = list(db.scalars(select(Notification).where(Notification.user_id == user.id)).all())
    assert len(rows) == 1
    assert rows[0].event_key == f"punt-created:{punt_id}"
    assert rows[0].event_type == "PUNT_CREATED"
    assert rows[0].resource_id == punt_id
    assert "Technical Product Manager" in rows[0].message
    assert "Example Employer" in rows[0].message


def test_application_status_notification_uses_persisted_event_and_is_idempotent(db):
    user, profile = make_candidate(db)
    job = make_job(db)
    now = datetime.now(timezone.utc)
    punt = Punt(
        candidate_profile_id=profile.id,
        job_id=job.id,
        status="ACTIVE",
        qualified_at=now,
    )
    db.add(punt)
    db.flush()
    application = Application(
        candidate_profile_id=profile.id,
        job_id=job.id,
        punt_id=punt.id,
        status="INTERVIEW",
        revision=2,
        created_at=now,
        updated_at=now,
    )
    db.add(application)
    db.flush()
    event = ApplicationEvent(
        application_id=application.id,
        event_type="APPLICATION_STATUS_CHANGED",
        from_status="SUBMITTED",
        to_status="INTERVIEW",
        actor_type="USER",
        actor_id=user.id,
        occurred_at=now,
        metadata_json={},
    )
    db.add(event)
    db.flush()
    event_id, application_id = event.id, application.id
    handle = application_status_notification_handler(handler_factory(db))

    handle({"application_event_id": str(event_id)})
    handle({"application_event_id": str(event_id)})

    rows = list(db.scalars(select(Notification).where(Notification.user_id == user.id)).all())
    assert len(rows) == 1
    assert rows[0].event_key == f"application-status:{event_id}"
    assert rows[0].event_type == "APPLICATION_STATUS_CHANGED"
    assert rows[0].resource_id == application_id
    assert "INTERVIEW" in rows[0].message


def test_notification_api_lists_and_marks_only_the_authenticated_users_rows(db):
    user, _profile = make_candidate(db)
    other_user, _other_profile = make_candidate(db)
    now = datetime.now(timezone.utc)
    own = Notification(
        user_id=user.id,
        event_key=f"test-own:{uuid4()}",
        event_type="PUNT_CREATED",
        title="Own notification",
        message="Your Punt is available.",
        resource_type="PUNT",
        resource_id=uuid4(),
        created_at=now,
    )
    foreign = Notification(
        user_id=other_user.id,
        event_key=f"test-foreign:{uuid4()}",
        event_type="PUNT_CREATED",
        title="Foreign notification",
        message="Not yours.",
        resource_type="PUNT",
        resource_id=uuid4(),
        created_at=now,
    )
    db.add_all([own, foreign])
    db.flush()
    own_id, foreign_id = own.id, foreign.id

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/v1/candidate/notifications")
        assert response.status_code == 200
        body = response.json()
        assert body["pagination"]["total"] == 1
        assert [item["id"] for item in body["data"]] == [str(own_id)]
        assert body["data"][0]["is_read"] is False

        marked = client.post(f"/api/v1/candidate/notifications/{own_id}/read")
        assert marked.status_code == 200
        assert marked.json()["data"]["is_read"] is True
        read_at = marked.json()["data"]["read_at"]
        repeated = client.post(f"/api/v1/candidate/notifications/{own_id}/read")
        assert repeated.status_code == 200
        assert repeated.json()["data"]["read_at"] == read_at

        forbidden = client.post(f"/api/v1/candidate/notifications/{foreign_id}/read")
        assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert NotificationService(db).list_for_user(user.id, unread_only=True)[1] == 0
