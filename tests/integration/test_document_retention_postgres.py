"""expire_eligible_documents against PostgreSQL: dependency-aware, audited, failure-detectable."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.application.documents.retention import expire_eligible_documents
from app.infrastructure.db.models import AuditEvent, CandidateProfile, Document, DocumentImport, User

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


class FakeStorage:
    def __init__(self, failing=()):
        self.deleted = []
        self.failing = set(failing)

    def delete(self, key):
        if key in self.failing:
            raise RuntimeError("storage unavailable: secret-detail")
        self.deleted.append(key)


def _document(db, profile, status="FAILED"):
    document = Document(
        candidate_profile_id=profile.id, document_type="resume", filename="private-name.txt",
        mime_type="text/plain", storage_key=f"retention/{uuid4()}", checksum_sha256="b" * 64,
        byte_size=5, metadata_json={},
    )
    db.add(document)
    db.flush()
    db.add(DocumentImport(candidate_profile_id=profile.id, document_id=document.id, status=status))
    db.flush()
    return document


def _profile(db):
    user = User(display_name=f"Retention {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id)
    db.add(profile)
    db.flush()
    return profile


def _events(db, document, event_type):
    return db.scalars(select(AuditEvent).where(
        AuditEvent.resource_id == document.id, AuditEvent.event_type == event_type
    )).all()


def test_expiry_deletes_only_eligible_documents_and_audits_without_private_values(db):
    profile = _profile(db)
    eligible = _document(db, profile, "FAILED")
    in_flight = _document(db, profile, "PROCESSING")
    now = datetime.now(timezone.utc) + timedelta(days=40)
    storage = FakeStorage()
    db.commit()

    expire_eligible_documents(db, storage, now=now)

    assert eligible.storage_key in storage.deleted
    assert in_flight.storage_key not in storage.deleted
    assert eligible.metadata_json["retention"]["status"] == "EXPIRED"
    events = _events(db, eligible, "DOCUMENT_RETENTION_EXPIRED")
    assert len(events) == 1 and events[0].actor_type == "SYSTEM"
    assert eligible.storage_key not in str(events[0].details) and "private-name" not in str(events[0].details)
    assert _events(db, in_flight, "DOCUMENT_RETENTION_EXPIRED") == []

    again = FakeStorage()
    expire_eligible_documents(db, again, now=now)
    assert eligible.storage_key not in again.deleted
    assert len(_events(db, eligible, "DOCUMENT_RETENTION_EXPIRED")) == 1


def test_storage_failure_keeps_the_record_and_leaves_a_detectable_failure(db):
    profile = _profile(db)
    document = _document(db, profile)
    now = datetime.now(timezone.utc) + timedelta(days=40)
    key = document.storage_key
    db.commit()  # the function under test rolls back; fixtures must be committed to the outer transaction

    result = expire_eligible_documents(db, FakeStorage(failing={key}), now=now)

    assert result["failures"] >= 1
    db.refresh(document)
    assert "retention" not in (document.metadata_json or {})
    failures = _events(db, document, "DOCUMENT_RETENTION_FAILED")
    assert len(failures) == 1
    assert failures[0].reason == "RuntimeError"
    assert failures[0].details["remote_object_deleted"] is False
    assert "secret-detail" not in str(failures[0].details) and key not in str(failures[0].details)
