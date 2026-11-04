"""Section G privacy matrix: the admin user-detail view is scoped to exactly one candidate."""

import os
from uuid import uuid4

import pytest

from app.api.deps import get_principal
from app.infrastructure.db.models.application_packages import ApplicationPackage, ApplicationPackageItem
from app.infrastructure.db.models.candidate import CandidateProfile
from app.infrastructure.db.models.documents import Document
from app.infrastructure.db.models.governance import AdminGrant
from app.infrastructure.db.models.identity import User
from app.infrastructure.db.models.jobs import Job
from app.infrastructure.db.models.notifications import Notification
from app.infrastructure.db.models.opportunities import Application, SavedJob
from app.infrastructure.db.models.preferences import CandidatePreferences
from app.infrastructure.db.models.support import SupportTicket
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


def _candidate(db, tag):
    user = User(display_name=f"{tag} display", email=f"{tag}@example.test", email_verified=True)
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE",
                               profile_data={"headline": f"{tag} headline"})
    db.add(profile)
    db.flush()
    job = Job(canonical_title=f"{tag} job title", company_name=f"{tag} company", lifecycle_status="ACTIVE",
              freshness_status="FRESH", application_url=f"https://example.test/{tag}",
              location_country="Nigeria", work_mode="REMOTE", metadata_json={}, location_restrictions={})
    db.add(job)
    db.flush()
    application = Application(candidate_profile_id=profile.id, job_id=job.id, status="PLANNED")
    db.add(application)
    db.flush()
    package = ApplicationPackage(application_id=application.id, version_number=1, status="DRAFT",
                                 source_snapshot={}, source_snapshot_hash="d" * 64, created_by_user_id=user.id)
    db.add(package)
    db.flush()
    item = ApplicationPackageItem(package_id=package.id, artifact_type="COVER_LETTER",
                                  content={"body": f"{tag} private artifact"}, evidence_refs=[],
                                  confidence="high", unsupported_claims=[], content_hash="e" * 64)
    db.add(item)
    db.add_all([
        SavedJob(candidate_profile_id=profile.id, job_id=job.id),
        CandidatePreferences(candidate_profile_id=profile.id, preferences={"marker": f"{tag} pref"}),
        Document(candidate_profile_id=profile.id, document_type="CV", filename=f"{tag}-cv.pdf",
                 mime_type="application/pdf", storage_key=f"private/{tag}/key",
                 checksum_sha256=("1" if tag == "alpha" else "2") * 64, byte_size=10),
        SupportTicket(candidate_user_id=user.id, idempotency_key=f"{tag}-{uuid4()}",
                      title=f"{tag} ticket title", issue_details=f"{tag} ticket body", status="OPEN"),
        Notification(user_id=user.id, event_key=f"{tag}-{uuid4()}", event_type="PUNT_QUALIFIED",
                     title=f"{tag} notification", message=f"{tag} notification body",
                     resource_type="punt", resource_id=uuid4()),
    ])
    db.flush()
    return user, profile


def _get(db, principal_id, target_id):
    client = _admin_test_client(db, principal_id)
    try:
        return client.get(f"/api/v1/admin/users/{target_id}")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_user_detail_never_contains_another_candidates_data_or_private_fields(db):  # noqa: F811
    admin = User(display_name="isolation admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    alpha, alpha_profile = _candidate(db, "alpha")
    beta, beta_profile = _candidate(db, "beta")

    response = _get(db, admin.id, alpha.id)
    assert response.status_code == 200, response.text
    body = response.text
    data = response.json()["data"]
    assert data["id"] == str(alpha.id)
    assert data["document_overview"]["total"] == 1
    assert data["application_overview"]["total"] == 1
    assert data["support_overview"]["total"] == 1
    assert data["notification_overview"]["total"] == 1

    # nothing identifying the other candidate appears anywhere in the response
    for leaked in (str(beta.id), str(beta_profile.id), "beta"):
        assert leaked not in body, leaked
    # private-field minimization holds per resource
    for private in ("alpha-cv.pdf", "private/alpha/key", "alpha ticket body", "alpha notification body",
                    "alpha private artifact", "1" * 64):
        assert private not in body, private

    # symmetric: the other candidate's view excludes alpha
    other = _get(db, admin.id, beta.id)
    assert other.status_code == 200
    assert str(alpha.id) not in other.text and "alpha" not in other.text


def test_user_detail_rejects_non_admin_and_unknown_user_without_leaking(db):  # noqa: F811
    outsider = User(display_name="plain user")
    db.add(outsider)
    db.flush()
    target, _ = _candidate(db, "alpha")

    denied = _get(db, outsider.id, target.id)
    assert denied.status_code in {401, 403}
    assert "alpha" not in denied.text

    admin = User(display_name="isolation admin 2")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    missing = _get(db, admin.id, uuid4())
    assert missing.status_code == 404, missing.text
