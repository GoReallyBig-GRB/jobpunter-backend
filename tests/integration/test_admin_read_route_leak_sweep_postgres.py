"""A0R-03: no parameterless admin GET route leaks candidate-private storage or identity values."""

import os
from uuid import uuid4

import pytest

from app.api.deps import get_principal
from app.api.v1 import admin as admin_api
from app.api.v1 import support as support_api
from app.infrastructure.db.models.application_packages import ApplicationPackage, ApplicationPackageItem
from app.infrastructure.db.models.candidate import CandidateProfile
from app.infrastructure.db.models.documents import Document
from app.infrastructure.db.models.governance import AdminGrant
from app.infrastructure.db.models.identity import Identity, User
from app.infrastructure.db.models.jobs import Job
from app.infrastructure.db.models.notifications import Notification
from app.infrastructure.db.models.opportunities import Application
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)

MARKERS = {
    "subject": "zz-provider-subject-marker",
    "filename": "zz-private-filename-marker.pdf",
    "storage": "zz/private/storage/key/marker",
    "checksum": "9" * 64,
    "notification": "zz notification body marker",
    "artifact": "zz private artifact body marker",
}


def _parameterless_get_paths():
    paths = []
    for route in list(admin_api.router.routes) + list(support_api.admin_router.routes):
        if "GET" in route.methods and "{" not in route.path:
            paths.append("/api/v1" + route.path)
    return sorted(set(paths))


def test_no_parameterless_admin_get_route_leaks_private_candidate_values(db):  # noqa: F811
    admin = User(display_name="sweep admin")
    candidate = User(display_name="sweep candidate", email="sweep@example.test", email_verified=True)
    db.add_all([admin, candidate])
    db.flush()
    db.add_all([
        AdminGrant(user_id=admin.id, role="ADMIN", active=True),
        Identity(user_id=candidate.id, provider="firebase", provider_subject=MARKERS["subject"]),
    ])
    profile = CandidateProfile(user_id=candidate.id, status="ACTIVE", profile_data={})
    db.add(profile)
    db.flush()
    job = Job(canonical_title="Sweep job", company_name="Sweep Co", lifecycle_status="ACTIVE",
              freshness_status="FRESH", application_url="https://example.test/sweep",
              location_country="Nigeria", work_mode="REMOTE", metadata_json={}, location_restrictions={})
    db.add(job)
    db.flush()
    application = Application(candidate_profile_id=profile.id, job_id=job.id, status="PLANNED")
    db.add(application)
    db.flush()
    package = ApplicationPackage(application_id=application.id, version_number=1, status="DRAFT",
                                 source_snapshot={}, source_snapshot_hash="d" * 64,
                                 created_by_user_id=candidate.id)
    db.add(package)
    db.flush()
    db.add_all([
        ApplicationPackageItem(package_id=package.id, artifact_type="COVER_LETTER",
                               content={"body": MARKERS["artifact"]}, evidence_refs=[],
                               confidence="high", unsupported_claims=[], content_hash="e" * 64),
        Document(candidate_profile_id=profile.id, document_type="CV", filename=MARKERS["filename"],
                 mime_type="application/pdf", storage_key=MARKERS["storage"],
                 checksum_sha256=MARKERS["checksum"], byte_size=10),
        Notification(user_id=candidate.id, event_key=f"sweep-{uuid4()}", event_type="PUNT_QUALIFIED",
                     title="sweep", message=MARKERS["notification"], resource_type="punt",
                     resource_id=uuid4()),
    ])
    db.flush()

    paths = _parameterless_get_paths()
    assert len(paths) >= 15, paths  # guards against the sweep silently shrinking
    client = _admin_test_client(db, admin.id)
    try:
        for path in paths:
            response = client.get(path)
            assert response.status_code < 500, (path, response.status_code, response.text[:200])
            for name, marker in MARKERS.items():
                assert marker not in response.text, f"{path} leaked {name}"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
