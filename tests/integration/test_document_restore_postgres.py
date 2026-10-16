import hashlib
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.documents.restore import DocumentRestoreService
from app.infrastructure.db.models import CandidateProfile, Document, User
from app.ports.object_storage import StoredObject

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


class MemoryObjectStorage:
    def __init__(self):
        self.objects = {}

    def get(self, key):
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    def put(self, key, content, content_type):
        if key in self.objects:
            raise FileExistsError(key)
        self.objects[key] = content
        return StoredObject(key, hashlib.sha256(content).hexdigest(), len(content))

    def delete(self, key):
        self.objects.pop(key, None)

    def create_download_url(self, key, expires_in_seconds):
        return f"https://private-storage.example.test/{key}?ttl={expires_in_seconds}"


class MemoryBackup:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def restore(self, *, file_id, document_id, checksum_sha256):
        self.calls.append((file_id, document_id, checksum_sha256))
        return self.content


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


def candidate(db):
    user = User(display_name=f"Restore Candidate {uuid4()}")
    db.add(user)
    db.flush()
    return user


def create_document(db, user, content, backup=None):
    profile = CandidateProfile(
        user_id=user.id, status="ACTIVE",
        profile_data={"identity": {"full_name": "Restore Test"}},
    )
    db.add(profile)
    db.flush()
    metadata = {"google_drive_backup": backup} if backup is not None else {}
    document = Document(
        candidate_profile_id=profile.id,
        application_package_id=None,
        application_package_item_id=None,
        document_type="master_cv",
        filename="master-v1.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_key=f"candidates/{profile.id}/cvs/master/v1/{uuid4().hex}.docx",
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        metadata_json=metadata,
    )
    db.add(document)
    db.flush()
    return profile, document


def test_restore_recovers_missing_private_document_and_records_result(db):
    user = candidate(db)
    content = b"verified CV bytes"
    _, document = create_document(db, user, content, {"status": "BACKED_UP", "file_id": "drive-file-1"})
    storage = MemoryObjectStorage()
    backup = MemoryBackup(content)
    result = DocumentRestoreService(db, storage=storage, backup_adapter=backup).restore(user.id, document.id)

    assert result["status"] == "RESTORED"
    assert storage.get(document.storage_key) == content
    assert backup.calls == [("drive-file-1", str(document.id), document.checksum_sha256)]
    refreshed = db.get(Document, document.id)
    assert refreshed.metadata_json["restore"]["status"] == "RESTORED"
    assert refreshed.metadata_json["restore"]["source"] == "GOOGLE_DRIVE_BACKUP"


def test_restore_returns_already_available_when_checksum_matches(db):
    user = candidate(db)
    content = b"already present"
    _, document = create_document(db, user, content, {"status": "BACKED_UP", "file_id": "drive-file-2"})
    storage = MemoryObjectStorage()
    storage.objects[document.storage_key] = content
    backup = MemoryBackup(b"unused")

    result = DocumentRestoreService(db, storage=storage, backup_adapter=backup).restore(user.id, document.id)
    assert result["status"] == "ALREADY_AVAILABLE"
    assert backup.calls == []


def test_restore_replaces_corrupt_object_only_after_backup_checksum_verifies(db):
    user = candidate(db)
    content = b"expected backup bytes"
    _, document = create_document(db, user, content, {"status": "BACKED_UP", "file_id": "drive-file-3"})
    storage = MemoryObjectStorage()
    storage.objects[document.storage_key] = b"corrupt object"
    backup = MemoryBackup(content)

    result = DocumentRestoreService(db, storage=storage, backup_adapter=backup).restore(user.id, document.id)
    assert result["status"] == "RESTORED"
    assert storage.get(document.storage_key) == content


def test_restore_rejects_corrupt_backup_without_writing_bytes(db):
    user = candidate(db)
    content = b"expected bytes"
    _, document = create_document(db, user, content, {"status": "BACKED_UP", "file_id": "drive-file-4"})
    storage = MemoryObjectStorage()
    backup = MemoryBackup(b"tampered backup")

    with pytest.raises(DomainError) as error:
        DocumentRestoreService(db, storage=storage, backup_adapter=backup).restore(user.id, document.id)
    assert error.value.code == "DOCUMENT_BACKUP_INTEGRITY_INVALID"
    assert storage.objects == {}


def test_restore_requires_a_recorded_backup_and_enforces_candidate_ownership(db):
    owner = candidate(db)
    stranger = candidate(db)
    content = b"private"
    _, document = create_document(db, owner, content, {"status": "FAILED", "error_code": "BACKUP_PROVIDER_ERROR"})
    storage = MemoryObjectStorage()
    backup = MemoryBackup(content)

    with pytest.raises(DomainError) as unavailable:
        DocumentRestoreService(db, storage=storage, backup_adapter=backup).restore(owner.id, document.id)
    assert unavailable.value.code == "DOCUMENT_RESTORE_UNAVAILABLE"

    with pytest.raises(DomainError) as forbidden:
        DocumentRestoreService(db, storage=storage, backup_adapter=backup).restore(stranger.id, document.id)
    assert forbidden.value.code == "DOCUMENT_NOT_FOUND"
    assert backup.calls == []
