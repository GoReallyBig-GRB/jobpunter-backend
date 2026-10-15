import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.infrastructure.db.models import Document, DocumentImport, User
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="PostgreSQL required")


def test_evidence_detail_exposes_saved_import_review_signals():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(user_id=user.id, auth_provider="test")
    client = TestClient(app)
    try:
        response = client.post("/api/v1/candidate/evidence", json={
            "evidence_type": "imported_document",
            "content": {"document_import_id": "pending"},
            "source_type": "candidate_upload",
            "source_reference": "test://evidence/import",
        })
        assert response.status_code == 201
        item = response.json()["data"]
        document = Document(
            candidate_profile_id=item["candidate_profile_id"],
            document_type="resume",
            filename="resume.txt",
            mime_type="text/plain",
            storage_key=f"test/{uuid4()}",
            checksum_sha256="a" * 64,
            byte_size=20,
            metadata_json={},
        )
        db.add(document)
        db.flush()
        db.add(DocumentImport(
            candidate_profile_id=item["candidate_profile_id"],
            document_id=document.id,
            status="SAVED",
            extracted_text="Role: Analyst",
            extracted_fields={"unresolved_items": [{"field": "employment_dates"}]},
            conflicts=[{"code": "POSSIBLE_EVIDENCE_OVERLAP"}],
            saved_evidence_id=item["id"],
        ))
        db.flush()

        detail = client.get(f"/api/v1/candidate/evidence/{item['id']}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["conflict_review_status"] == "REVIEW_REQUIRED"
        assert data["conflicts"][0]["code"] == "POSSIBLE_EVIDENCE_OVERLAP"
        assert data["missing_information_status"] == "REVIEW_REQUIRED"
        assert data["missing_information"][0]["field"] == "employment_dates"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
