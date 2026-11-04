"""K2.4: storage usage is observed from document metadata, labelled honestly and alertable."""
import os
from uuid import uuid4

import pytest

from app.api.deps import get_principal
from app.config.settings import get_settings
from app.infrastructure.db.models import CandidateProfile, Document, User
from app.infrastructure.db.models.governance import AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)
URL = "/api/v1/admin/operations/storage-usage"


def _admin(db):
    admin = User(display_name="usage admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    return admin


def _doc(db, profile, size, kind="CV", expired=False):
    doc = Document(
        candidate_profile_id=profile.id, document_type=kind, filename="private-name.pdf",
        mime_type="application/pdf", storage_key=f"private/{uuid4()}", checksum_sha256="a" * 64,
        byte_size=size, metadata_json={"retention": {"status": "EXPIRED"}} if expired else {},
    )
    db.add(doc)
    db.flush()
    return doc


def _get(db, principal_id):
    client = _admin_test_client(db, principal_id)
    try:
        return client.get(URL)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def _profile(db):
    user = User(display_name="doc owner")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return profile


def test_non_admin_is_denied(db):  # noqa: F811
    user = User(display_name="plain")
    db.add(user)
    db.flush()
    assert _get(db, user.id).status_code in {401, 403}


def test_usage_counts_live_documents_excludes_expired_and_hides_private_fields(db, monkeypatch):  # noqa: F811
    admin = _admin(db)
    before = _get(db, admin.id).json()["data"]["live"]
    profile = _profile(db)
    _doc(db, profile, 1000, "CV")
    _doc(db, profile, 500, "CV")
    _doc(db, profile, 250, "UPLOAD")
    _doc(db, profile, 9999, "CV", expired=True)
    response = _get(db, admin.id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["live"]["documents"] - before["documents"] == 3
    assert data["live"]["bytes"] - before["bytes"] == 1750
    assert data["live"]["bytes_by_document_type"]["CV"] - before["bytes_by_document_type"].get("CV", 0) == 1500
    assert data["expired_documents"] >= 1
    assert data["basis"] == "DATABASE_METADATA"
    assert data["provider_billed_usage"] == "NOT_MEASURED"
    assert data["retention_days"] == get_settings().document_retention_days
    for private in ("private-name.pdf", "private/", "a" * 64):
        assert private not in response.text


def test_alert_level_reflects_configured_thresholds(db, monkeypatch):  # noqa: F811
    admin = _admin(db)
    used = _get(db, admin.id).json()["data"]["live"]["bytes"]
    settings = get_settings()
    assert _get(db, admin.id).json()["data"]["alert"]["level"] == "NOT_CONFIGURED"
    monkeypatch.setattr(settings, "storage_usage_warning_bytes", used + 10_000)
    monkeypatch.setattr(settings, "storage_usage_critical_bytes", used + 20_000)
    assert _get(db, admin.id).json()["data"]["alert"]["level"] == "OK"
    _doc(db, _profile(db), 15_000)
    assert _get(db, admin.id).json()["data"]["alert"]["level"] == "WARNING"
    _doc(db, _profile(db), 10_000)
    alert = _get(db, admin.id).json()["data"]["alert"]
    assert alert["level"] == "CRITICAL" and alert["critical_bytes"] == used + 20_000
