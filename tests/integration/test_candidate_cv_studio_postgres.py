import hashlib
import json
import os
from io import BytesIO
from uuid import uuid4

import fitz
from docx import Document as DocxDocument

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import DBAPIError
from app.ports.object_storage import StoredObject
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.cv_studio.service import CVStudioService
from app.application.cv_studio.documents import CVDocumentService
from app.infrastructure.db.models import (
    CandidateCVApproval, CandidateCVVersion, CandidateProfile, Document, User,
)

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


def candidate(db, profile_data=None):
    user = User(display_name=f"CV Candidate {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(
        user_id=user.id, status="ACTIVE", revision=1,
        profile_data=profile_data or {
            "identity": {"full_name": "Ada Example"},
            "professional_identity": {"current_role": "Product Manager"},
            "career_history": [{"employer": "Example Co", "role": "Product Manager"}],
        },
    )
    db.add(profile)
    db.flush()
    return user, profile


def test_master_cv_regeneration_is_versioned_and_never_mutates_profile(db):
    profile_data = {
        "identity": {"full_name": "Ada Example"},
        "professional_identity": {"current_role": "Product Manager"},
        "career_history": [{"employer": "Example Co", "achievements": ["Reduced onboarding time"]}],
    }
    user, profile = candidate(db, profile_data)
    before = dict(profile.profile_data)
    service = CVStudioService(db)

    first = service.regenerate_master(user.id, expected_profile_revision=1)
    assert first.version_number == 1
    assert first.cv_type == "MASTER"
    assert first.content == before
    assert first.source_profile_snapshot == before
    assert first.source_profile_revision == 1
    assert profile.profile_data == before

    profile.profile_data = {
        **profile.profile_data,
        "professional_identity": {"current_role": "Senior Product Manager"},
    }
    profile.revision = 2
    db.commit()

    second = service.regenerate_master(user.id, expected_profile_revision=2)
    assert second.version_number == 2
    assert second.content["professional_identity"]["current_role"] == "Senior Product Manager"
    old = db.get(CandidateCVVersion, first.id)
    assert old.content["professional_identity"]["current_role"] == "Product Manager"
    assert old.source_profile_revision == 1


def test_variant_versions_are_independent_from_master_and_conflicts_are_rejected(db):
    user, profile = candidate(db)
    service = CVStudioService(db)
    master = service.regenerate_master(user.id, expected_profile_revision=1)

    variant = service.create_variant(
        user.id, "product-management", "Product Management CV", master.id,
        content={"title": "Product Management CV", "summary": "Candidate-edited summary"},
    )
    assert variant.version_number == 1
    assert variant.source_master_version_id == master.id

    saved = service.save_version(
        user.id, "product-management", expected_latest_version=1,
        content={"title": "Product Management CV", "summary": "Revised summary"},
    )
    assert saved.version_number == 2
    assert db.get(CandidateCVVersion, variant.id).content["summary"] == "Candidate-edited summary"
    assert db.get(CandidateCVVersion, master.id).content == master.content

    with pytest.raises(DomainError) as conflict:
        service.save_version(
            user.id, "product-management", expected_latest_version=1,
            content={"summary": "Stale overwrite"},
        )
    assert conflict.value.code == "REVISION_CONFLICT"


def test_cv_approval_is_candidate_attributed_and_bound_to_exact_version(db):
    user, _ = candidate(db)
    service = CVStudioService(db)
    master = service.regenerate_master(user.id, expected_profile_revision=1)
    approval = service.approve_version(user.id, "master", 1, "Reviewed by candidate")
    assert approval.cv_version_id == master.id
    assert approval.approved_by_user_id == user.id
    assert approval.notes == "Reviewed by candidate"

    # Approval is idempotent for the same immutable version.
    same_approval = service.approve_version(user.id, "master", 1, "Different retry note")
    assert same_approval.id == approval.id
    next_version = service.save_version(
        user.id, "master", expected_latest_version=1,
        content={"identity": {"full_name": "Ada Example"}, "summary": "Edited"},
    )
    assert next_version.version_number == 2
    assert db.scalar(select(CandidateCVApproval).where(
        CandidateCVApproval.cv_version_id == next_version.id
    )) is None


def test_cv_edit_history_preserves_content_hash_source_provenance_and_exact_approval(db):
    user, profile = candidate(db)
    service = CVStudioService(db)

    master = service.regenerate_master(user.id, expected_profile_revision=1)
    master_hash = hashlib.sha256(
        json.dumps(master.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert master.content_hash == master_hash
    assert master.source_profile_hash == master_hash
    assert master.source_profile_revision == profile.revision

    variant = service.create_variant(
        user.id, "product-management", "Product Management CV", master.id,
        content={"summary": "Evidence-based summary", "experience": [{"employer": "Example Co"}]},
    )
    original_snapshot = variant.source_profile_snapshot
    original_snapshot_hash = variant.source_profile_hash

    service.approve_version(user.id, "product-management", 1, "Reviewed version one")
    edited = service.save_version(
        user.id, "product-management", expected_latest_version=1,
        title="Product Management CV - revised",
        content={"summary": "Revised evidence-based summary", "experience": [{"employer": "Example Co"}]},
    )
    history = service.get_cv(user.id, "product-management")

    assert history["latest"]["version_number"] == 2
    assert [item["version_number"] for item in history["versions"]] == [2, 1]
    assert history["versions"][0]["content_hash"] == edited.content_hash
    assert history["versions"][1]["content_hash"] == variant.content_hash
    assert history["versions"][1]["approved"] is True
    assert history["versions"][0]["approved"] is False
    assert edited.source_profile_revision == variant.source_profile_revision
    assert edited.source_profile_snapshot == original_snapshot
    assert edited.source_profile_hash == original_snapshot_hash
    assert edited.source_master_version_id == master.id
    assert history["versions"][1]["source_master_version_id"] == str(master.id)
    assert history["versions"][1]["content"]["summary"] == "Evidence-based summary"
    assert history["versions"][0]["content"]["summary"] == "Revised evidence-based summary"


def test_cv_versions_are_candidate_scoped(db):
    owner, _ = candidate(db)
    stranger, _ = candidate(db)
    master = CVStudioService(db).regenerate_master(owner.id, expected_profile_revision=1)
    with pytest.raises(DomainError) as missing:
        CVStudioService(db).get_cv(stranger.id, "master")
    assert missing.value.code == "CV_NOT_FOUND"
    with pytest.raises(DomainError) as wrong_master:
        CVStudioService(db).create_variant(
            stranger.id, "other-cv", "Other CV", master.id,
        )
    assert wrong_master.value.code == "MASTER_CV_VERSION_NOT_FOUND"


def test_cv_version_rows_are_append_only_at_database_level(db):
    user, _ = candidate(db)
    version = CVStudioService(db).regenerate_master(user.id, expected_profile_revision=1)
    nested = db.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            db.execute(
                CandidateCVVersion.__table__.update()
                .where(CandidateCVVersion.id == version.id)
                .values(title="Tampered")
            )
    finally:
        nested.rollback()
    assert db.get(CandidateCVVersion, version.id).title == "Ada Example"


def test_cv_key_format_remains_enforced_without_database_specific_constraints(db):
    user, _ = candidate(db)
    service = CVStudioService(db)
    with pytest.raises(DomainError) as create_error:
        service.create_variant(user.id, "Not A Key", "Invalid", uuid4())
    assert create_error.value.code == "CV_KEY_INVALID"
    with pytest.raises(DomainError) as save_error:
        service.save_version(
            user.id, "Not A Key", expected_latest_version=1,
            content={"summary": "Invalid key"},
        )
    assert save_error.value.code == "CV_KEY_INVALID"


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


def test_approved_master_cv_renders_real_private_docx_pdf_and_scoped_downloads(db):
    user, _ = candidate(db)
    storage = MemoryObjectStorage()
    cv_service = CVStudioService(db)
    master = cv_service.regenerate_master(user.id, expected_profile_revision=1)
    cv_service.approve_version(user.id, "master", 1, "Reviewed before download")
    documents = CVDocumentService(db, storage=storage)

    docx = documents.generate(user.id, "master", 1, "docx")
    pdf = documents.generate(user.id, "master", 1, "pdf")
    assert storage.get(docx.storage_key).startswith(b"PK")
    assert storage.get(pdf.storage_key).startswith(b"%PDF-")
    assert docx.checksum_sha256 == hashlib.sha256(storage.get(docx.storage_key)).hexdigest()
    assert pdf.checksum_sha256 == hashlib.sha256(storage.get(pdf.storage_key)).hexdigest()
    assert docx.document_type == "master_cv"
    assert docx.metadata_json["cv_version_id"] == str(master.id)
    assert docx.application_package_id is None

    download = documents.create_download_url(user.id, "master", 1, docx.id)
    assert download["document_id"] == str(docx.id)
    assert download["expires_in_seconds"] >= 1
    assert "private-storage.example.test" in download["download_url"]


def test_cv_document_download_and_generation_require_owned_approved_versions(db):
    owner, _ = candidate(db)
    stranger, _ = candidate(db)
    storage = MemoryObjectStorage()
    service = CVStudioService(db)
    master = service.regenerate_master(owner.id, expected_profile_revision=1)
    documents = CVDocumentService(db, storage=storage)

    with pytest.raises(DomainError) as unapproved:
        documents.generate(owner.id, "master", 1, "docx")
    assert unapproved.value.code == "CV_VERSION_NOT_APPROVED"

    service.approve_version(owner.id, "master", 1)
    document = documents.generate(owner.id, "master", 1, "docx")
    with pytest.raises(DomainError) as wrong_owner:
        documents.create_download_url(stranger.id, "master", 1, document.id)
    assert wrong_owner.value.code == "CV_VERSION_NOT_FOUND"

    with pytest.raises(DomainError) as wrong_document:
        documents.create_download_url(owner.id, "master", 1, uuid4())
    assert wrong_document.value.code == "DOCUMENT_NOT_FOUND"



def test_cv_rendered_artifacts_contain_real_cv_text_and_reject_tampered_storage_references(db):
    user, _ = candidate(db, {
        "identity": {"full_name": "Ada Example"},
        "professional_identity": {"current_role": "Product Manager"},
        "career_history": [{
            "employer": "Example Co",
            "role": "Product Manager",
            "achievements": ["Reduced onboarding time by 35 percent"],
        }],
        "skills_tools": ["Roadmapping", "Delivery leadership"],
    })
    storage = MemoryObjectStorage()
    service = CVStudioService(db)
    master = service.regenerate_master(user.id, expected_profile_revision=1)
    service.approve_version(user.id, "master", 1, "Approved for artifact validation")
    documents = CVDocumentService(db, storage=storage)

    docx = documents.generate(user.id, "master", 1, "docx")
    pdf = documents.generate(user.id, "master", 1, "pdf")
    docx_bytes = storage.get(docx.storage_key)
    pdf_bytes = storage.get(pdf.storage_key)

    parsed_docx = DocxDocument(BytesIO(docx_bytes))
    docx_text = "\n".join(paragraph.text for paragraph in parsed_docx.paragraphs)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as parsed_pdf:
        pdf_text = "\n".join(page.get_text() for page in parsed_pdf)

    for rendered_text in (docx_text, pdf_text):
        assert "Ada Example" in rendered_text
        assert "Product Manager" in rendered_text
        assert "Example Co" in rendered_text
        assert "Reduced onboarding time by 35 percent" in rendered_text
        assert "Roadmapping" in rendered_text

    assert docx.mime_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert pdf.mime_type == "application/pdf"
    assert docx.byte_size == len(docx_bytes) > 0
    assert pdf.byte_size == len(pdf_bytes) > 0
    assert docx.checksum_sha256 == hashlib.sha256(docx_bytes).hexdigest()
    assert pdf.checksum_sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert docx.metadata_json["cv_version_id"] == str(master.id)
    assert pdf.metadata_json["cv_version_id"] == str(master.id)

    # A valid document ID is not sufficient if its stored object key escapes
    # the candidate/CV/version namespace bound to the database record.
    original_key = docx.storage_key
    docx.storage_key = f"candidates/{uuid4()}/cvs/master/v1/{uuid4().hex}.docx"
    db.commit()
    with pytest.raises(DomainError) as tampered_reference:
        documents.create_download_url(user.id, "master", 1, docx.id)
    assert tampered_reference.value.code == "DOCUMENT_STORAGE_REFERENCE_INVALID"
    docx.storage_key = original_key
    db.commit()

    # Restore a legitimate reference, then prove the exact document remains
    # candidate-scoped even when the caller knows the document UUID.
    stranger, _ = candidate(db)
    with pytest.raises(DomainError) as wrong_owner:
        documents.create_download_url(stranger.id, "master", 1, docx.id)
    assert wrong_owner.value.code == "CV_VERSION_NOT_FOUND"
