import hashlib
import os
import asyncio
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.documents.imports import DocumentImportService
from app.infrastructure.db.models import (
    CandidateProfile, DocumentImport, Evidence, EvidenceSource, User,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal
from app.ports.object_storage import StoredObject

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


class MemoryObjectStorage:
    def __init__(self):
        self.objects = {}

    def put(self, storage_key, content, content_type):
        self.objects[storage_key] = content
        return StoredObject(storage_key, hashlib.sha256(content).hexdigest(), len(content))

    def get(self, storage_key):
        return self.objects[storage_key]

    def delete(self, storage_key):
        self.objects.pop(storage_key, None)

    def create_download_url(self, storage_key, expires_in_seconds):
        return f"https://private-storage.example.test/{storage_key}?ttl={expires_in_seconds}"


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
    user = User(display_name=f"Import Candidate {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return user, profile


def test_import_requires_candidate_confirmation_and_saves_only_draft_evidence(db):
    user, profile = candidate(db)
    storage = MemoryObjectStorage()
    service = DocumentImportService(db, storage=storage)
    record = service.upload(
        user.id,
        "resume.txt",
        "text/plain",
        b"Technical Product Manager at Example Company. Led product delivery.",
    )

    assert record.status == "UPLOADED"
    assert db.scalar(select(Evidence).where(Evidence.candidate_profile_id == profile.id)) is None

    parsed = service.parse(user.id, record.id)
    assert parsed.status == "PARSED"
    assert "Technical Product Manager" in parsed.extracted_text
    preview = service.preview(user.id, record.id)
    assert preview.status == "PREVIEWED"

    conflicts = service.identify_conflicts(user.id, record.id)
    assert conflicts.status == "CONFLICTS_IDENTIFIED"
    with pytest.raises(Exception) as error:
        service.save(user.id, record.id)
    assert getattr(error.value, "code", None) == "DOCUMENT_IMPORT_CONFIRMATION_REQUIRED"

    confirmed = service.confirm(
        user.id, record.id, expected_revision=conflicts.revision
    )
    assert confirmed.status == "CONFIRMED"
    assert confirmed.confirmed_by_user_id == user.id

    saved, evidence = service.save(user.id, record.id)
    assert saved.status == "SAVED"
    assert saved.saved_evidence_id == evidence.id
    assert evidence.status == "DRAFT"
    assert evidence.evidence_type == "imported_document"
    assert evidence.content["document_import_id"] == str(record.id)
    assert evidence.source.source_reference == f"document-import:{record.id}"

    saved_again, evidence_again = service.save(user.id, record.id)
    assert saved_again.id == saved.id
    assert evidence_again.id == evidence.id
    assert len(db.scalars(select(Evidence).where(
        Evidence.candidate_profile_id == profile.id,
    )).all()) == 1


def test_import_conflict_overlap_requires_explicit_review(db):
    user, profile = candidate(db)
    source = EvidenceSource(
        source_type="candidate_upload",
        source_reference=f"test://existing/{uuid4()}",
        metadata_json={},
    )
    db.add(source)
    db.flush()
    existing = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="employment_record",
        content={"statement": "Product Manager at Example Company"},
    )
    db.add(existing)
    db.flush()

    service = DocumentImportService(db, storage=MemoryObjectStorage())
    record = service.upload(
        user.id,
        "resume.txt",
        "text/plain",
        b"Career record: Product Manager at Example Company since 2020.",
    )
    service.parse(user.id, record.id)
    service.preview(user.id, record.id)
    identified = service.identify_conflicts(user.id, record.id)

    assert identified.status == "CONFLICTS_IDENTIFIED"
    assert identified.conflicts[0]["code"] == "POSSIBLE_EVIDENCE_OVERLAP"
    with pytest.raises(Exception) as error:
        service.confirm(user.id, record.id, expected_revision=identified.revision)
    assert getattr(error.value, "code", None) == "DOCUMENT_CONFLICT_REVIEW_REQUIRED"

    confirmed = service.confirm(
        user.id, record.id, expected_revision=identified.revision, conflicts_reviewed=True
    )
    assert confirmed.status == "CONFIRMED"


def test_document_import_is_candidate_scoped(db):
    owner, _ = candidate(db)
    other_user, _ = candidate(db)
    service = DocumentImportService(db, storage=MemoryObjectStorage())
    record = service.upload(owner.id, "resume.txt", "text/plain", b"Candidate evidence text.")

    from app.api.errors import DomainError
    with pytest.raises(DomainError) as error:
        service.parse(other_user.id, record.id)
    assert error.value.code == "DOCUMENT_IMPORT_NOT_FOUND"

    with pytest.raises(DomainError) as extraction_error:
        asyncio.run(service.extract_fields(other_user.id, record.id))
    assert extraction_error.value.code == "DOCUMENT_IMPORT_NOT_FOUND"


def test_document_import_api_upload_preview_confirm_and_save(db, monkeypatch):
    import app.api.v1.document_imports as import_routes

    async def fake_execute_ai_task(request, *, db, user_id):
        assert request.task == "document_parsing"
        return SimpleNamespace(parsed_data={
            "document_type": "resume",
            "fields": [{
                "name": "full_name",
                "value": "Example Candidate",
                "source_text": "Name: Example Candidate",
                "confidence": "high",
            }],
            "conflicts": [],
            "unresolved_items": [],
        })

    monkeypatch.setattr(
        "app.application.documents.imports.execute_ai_task",
        fake_execute_ai_task,
    )
    user, profile = candidate(db)
    storage = MemoryObjectStorage()
    monkeypatch.setattr(
        import_routes,
        "DocumentImportService",
        lambda session: DocumentImportService(session, storage=storage),
    )
    connection = db.connection()

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    client = TestClient(app)
    try:
        uploaded = client.post(
            "/api/v1/candidate/document-imports",
            files={"file": ("resume.txt", b"Technical Product Manager at Example Company.", "text/plain")},
        )
        assert uploaded.status_code == 201
        record_id = uploaded.json()["data"]["id"]
        assert uploaded.json()["data"]["status"] == "UPLOADED"

        parsed = client.post(f"/api/v1/candidate/document-imports/{record_id}/parse")
        assert parsed.status_code == 200
        assert parsed.json()["data"]["status"] == "PARSED"
        assert "Technical Product Manager" in parsed.json()["data"]["extracted_text"]

        extracted = client.post(
            f"/api/v1/candidate/document-imports/{record_id}/extract-fields"
        )
        assert extracted.status_code == 200
        assert extracted.json()["data"]["status"] == "PREVIEWED"
        assert extracted.json()["data"]["extracted_fields"]["fields"][0]["value"] == "Example Candidate"

        preview = client.get(f"/api/v1/candidate/document-imports/{record_id}/preview")
        assert preview.status_code == 200
        assert preview.json()["data"]["status"] == "PREVIEWED"

        conflicts = client.post(
            f"/api/v1/candidate/document-imports/{record_id}/identify-conflicts"
        )
        assert conflicts.status_code == 200
        assert conflicts.json()["data"]["status"] == "CONFLICTS_IDENTIFIED"

        confirmed = client.post(
            f"/api/v1/candidate/document-imports/{record_id}/confirm",
            json={
                "expected_revision": conflicts.json()["data"]["revision"],
                "conflicts_reviewed": True,
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["data"]["status"] == "CONFIRMED"

        saved = client.post(f"/api/v1/candidate/document-imports/{record_id}/save")
        assert saved.status_code == 200
        data = saved.json()["data"]
        assert data["status"] == "SAVED"
        assert data["evidence"]["status"] == "DRAFT"
        assert data["evidence"]["id"]
        assert db.scalar(select(Evidence).where(
            Evidence.candidate_profile_id == profile.id,
            Evidence.status == "DRAFT",
        )) is not None
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()
