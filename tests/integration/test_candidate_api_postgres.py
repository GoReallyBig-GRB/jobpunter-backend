import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.infrastructure.db.models import CandidateProfile, CandidateProfileFactProvenance, User
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


def test_fastapi_candidate_evidence_journey_and_cross_user_denial(db):
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
        profile_response = client.get("/api/v1/candidate/profile")
        assert profile_response.status_code == 200
        profile_id = profile_response.json()["data"]["id"]
        persisted_profile = db.scalar(
            select(CandidateProfile).where(CandidateProfile.id == profile_id)
        )
        assert persisted_profile is not None
        assert persisted_profile.user_id == owner.id
        assert profile_response.json()["data"]["profile_data"]["career_history"] == []

        profile_update = client.patch(
            "/api/v1/candidate/profile",
            json={
                "expected_revision": 1,
                "sections": {
                    "identity": {"full_name": "Test Candidate"},
                    "career_history": [{"role": "Analyst", "employer": "Example Co"}],
                },
            },
        )
        assert profile_update.status_code == 200
        assert profile_update.json()["data"]["revision"] == 2

        provenance_response = client.get("/api/v1/candidate/profile/provenance")
        assert provenance_response.status_code == 200
        provenance = provenance_response.json()["data"]
        identity_fact = next(
            item for item in provenance
            if item["section"] == "identity" and item["fact_path"] == "full_name"
        )
        assert identity_fact["fact_value"] == "Test Candidate"
        assert identity_fact["source_type"] == "CANDIDATE_INPUT"
        assert identity_fact["profile_revision"] == 2
        assert identity_fact["confirmed_by_user_id"] == str(owner.id)
        persisted_provenance = db.scalar(
            select(CandidateProfileFactProvenance).where(
                CandidateProfileFactProvenance.candidate_profile_id == profile_id,
                CandidateProfileFactProvenance.section == "identity",
                CandidateProfileFactProvenance.fact_path == "full_name",
            )
        )
        assert persisted_provenance is not None

        partial_update = client.patch(
            "/api/v1/candidate/profile",
            json={
                "expected_revision": 2,
                "sections": {"skills_tools": [{"name": "SQL", "level": "working"}]},
            },
        )
        assert partial_update.status_code == 200
        profile_data = partial_update.json()["data"]["profile_data"]
        assert profile_data["identity"]["full_name"] == "Test Candidate"
        assert profile_data["career_history"][0]["role"] == "Analyst"
        assert profile_data["skills_tools"][0]["name"] == "SQL"
        provenance_history = client.get("/api/v1/candidate/profile/provenance").json()["data"]
        assert any(
            item["section"] == "identity"
            and item["fact_path"] == "full_name"
            and item["profile_revision"] == 2
            for item in provenance_history
        )
        assert any(
            item["section"] == "skills_tools"
            and item["fact_path"] == "[0].name"
            and item["profile_revision"] == 3
            for item in provenance_history
        )

        # Profile sections are isolated by the authenticated candidate, not a client-supplied ID.
        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other_user.id, auth_provider="test"
        )
        other_profile = client.get("/api/v1/candidate/profile")
        assert other_profile.status_code == 200
        assert other_profile.json()["data"]["profile_data"]["identity"] == {}
        other_provenance = client.get("/api/v1/candidate/profile/provenance")
        assert other_provenance.status_code == 200
        assert other_provenance.json()["data"] == []
        other_update = client.patch(
            "/api/v1/candidate/profile",
            json={"expected_revision": 1, "sections": {"identity": {"full_name": "Other Candidate"}}},
        )
        assert other_update.status_code == 200
        assert other_update.json()["data"]["profile_data"]["identity"]["full_name"] == "Other Candidate"

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=owner.id, auth_provider="test"
        )
        owner_profile = client.get("/api/v1/candidate/profile")
        assert owner_profile.json()["data"]["profile_data"]["identity"]["full_name"] == "Test Candidate"

        stale_profile_update = client.patch(
            "/api/v1/candidate/profile",
            json={"expected_revision": 1, "sections": {"identity": {"full_name": "Stale"}}},
        )
        assert stale_profile_update.status_code == 409
        assert stale_profile_update.json()["error"]["code"] == "REVISION_CONFLICT"

        create_response = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "employment_record",
                "content": {"role": "Product Manager"},
                "source_type": "candidate_upload",
                "source_reference": f"test://evidence/{uuid4()}",
                "source_metadata": {"test": True},
            },
        )
        assert create_response.status_code == 201
        created = create_response.json()["data"]
        assert created["candidate_profile_id"] == profile_id
        assert created["status"] == "DRAFT"
        assert created["revision"] == 1
        evidence_id = created["id"]

        submitted = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/submit",
            json={"expected_revision": 1},
        )
        assert submitted.status_code == 200
        assert submitted.json()["data"]["status"] == "PENDING_REVIEW"
        assert submitted.json()["data"]["revision"] == 2

        approved = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/approve",
            json={"expected_revision": 2},
        )
        assert approved.status_code == 200
        assert approved.json()["data"]["status"] == "APPROVED"
        assert approved.json()["data"]["revision"] == 3
        assert approved.json()["data"]["verified_at"] is not None

        stale = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/archive",
            json={"expected_revision": 2},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other_user.id, auth_provider="test"
        )
        foreign = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/archive",
            json={"expected_revision": 3},
        )
        assert foreign.status_code == 404
        assert foreign.json()["error"]["code"] == "NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

def test_prd_master_profile_fields_round_trip_and_section_clear_are_truthful(db):
    owner = make_user(db)
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
    payload = {
        "expected_revision": 1,
        "sections": {
            "identity": {
                "full_name": "Test Candidate",
                "preferred_name": "Test",
                "email": "candidate@example.test",
                "phone": "+2348000000000",
                "current_location": "Port Harcourt, Nigeria",
                "country": "Nigeria",
                "linkedin_url": "https://linkedin.example.test/candidate",
                "portfolio_url": "https://portfolio.example.test/candidate",
                "github_url": "https://github.example.test/candidate",
                "website_url": "https://candidate.example.test",
            },
            "eligibility": {
                "work_authorizations": ["Nigeria"],
                "eligible_countries": ["Nigeria"],
                "work_authorization_countries": ["Nigeria"],
                "languages": ["English"],
                "professional_credentials": ["Professional credential"],
                "relocation_status": "NOT_WILLING",
                "notes": "Work authorization and language facts are candidate supplied.",
            },
            "professional_identity": {
                "profession": "Product Management",
                "current_role": "Product Director",
                "years_experience": 12,
                "summary": "Technical product delivery leader",
                "headline": "Product Director",
                "industries": ["Technology"],
                "domains": ["B2B SaaS"],
                "functional_areas": ["Product delivery"],
                "seniority": "Director",
                "specializations": ["Platform products", "Workflow automation"],
            },
            "career_history": [{
                "name": "Example role record",
                "title": "Product Director",
                "role": "Product owner",
                "summary": "Owned product delivery and roadmap execution",
                "status": "CURRENT",
                "employer": "Example Employer",
                "company": "Example Employer Ltd",
                "organization": "Example Group",
                "job_title": "Product Director",
                "start_date": "2020-01",
                "end_date": None,
                "employment_type": "FULL_TIME",
                "industries": ["Technology"],
                "domains": ["B2B SaaS"],
                "responsibilities": ["Own product delivery"],
                "products": ["Internal platform"],
                "projects": ["Workflow rebuild"],
                "users": ["SMEs"],
                "customers": ["Business customers"],
                "candidate_role": "Product owner",
                "ownership": "Roadmap and delivery",
                "tools": ["Jira"],
                "technologies": ["REST APIs"],
                "stakeholders": ["Engineering", "Operations"],
                "teams": ["Product and engineering"],
                "leadership_responsibilities": ["Coordinate delivery"],
                "achievements": ["Improved workflow"],
                "challenges": ["Legacy process"],
                "outcomes": [{"value": None, "verification_status": "UNVERIFIED"}],
                "evidence": [{"source": "candidate_statement"}],
                "verification_status": "UNVERIFIED",
            }],
            "projects": [{
                "name": "Workflow rebuild",
                "title": "Workflow rebuild project",
                "role": "Product owner",
                "summary": "Modernized cross-team operations",
                "status": "SHIPPED",
                "start_date": "2023-01",
                "end_date": "2023-09",
                "verification_status": "UNVERIFIED",
                "product": "Internal operations platform",
                "description": "Rebuilt an internal workflow",
                "problem": "Teams coordinated work manually",
                "problem_addressed": "Manual coordination",
                "context": "Growing cross-functional team",
                "users": ["SMEs"],
                "customers": ["Business customers"],
                "stakeholders": ["Operations"],
                "candidate_role": "Product owner",
                "responsibilities": ["Define requirements"],
                "ownership": "Product discovery through delivery",
                "decisions": ["Use an API-first design"],
                "constraints": ["Limited engineering capacity"],
                "challenges": ["Legacy data"],
                "solution": "Incremental migration",
                "tools": ["Jira"],
                "technologies": ["REST APIs"],
                "outcomes": [{"value": None, "verification_status": "UNVERIFIED"}],
                "results": [{"value": None, "verification_status": "UNVERIFIED"}],
                "evidence": [],
                "product_status": "prototype",
            }],
            "achievements": [{
                "name": "Support workflow achievement",
                "title": "Support workflow improvement",
                "role": "Product owner",
                "summary": "Introduced a consistent triage process",
                "status": "DOCUMENTED",
                "start_date": "2024-01",
                "end_date": "2024-03",
                "verification_status": "UNVERIFIED",
                "achievement": "Improved support workflow",
                "context": "Requests were handled manually",
                "action": "Introduced a triage workflow",
                "outcome": None,
                "metrics": None,
                "impact": "More consistent support triage",
                "results": [{"value": None, "verification_status": "UNVERIFIED"}],
                "source": {"kind": "candidate_statement"},
                "evidence": [{"source": "candidate_statement"}],
            }],
            "challenges": [{
                "name": "Legacy workflow challenge",
                "title": "Legacy workflow",
                "role": "Product owner",
                "summary": "Resolved inconsistent cross-team handoffs",
                "status": "DOCUMENTED",
                "start_date": "2022-01",
                "end_date": "2022-06",
                "verification_status": "UNVERIFIED",
                "challenge": "Legacy workflow",
                "problem": "Multiple teams used different processes",
                "context": "Multiple teams used different processes",
                "constraints": ["Limited engineering capacity"],
                "solution": "Standardized handoffs",
                "decisions": ["Adopt one shared workflow"],
                "outcomes": [{"value": None, "verification_status": "UNVERIFIED"}],
                "results": [{"value": None, "verification_status": "UNVERIFIED"}],
                "tools": ["Jira"],
                "evidence": [{"source": "candidate_statement"}],
            }],
            "leadership": [{
                "name": "Delivery leadership",
                "title": "Product Director",
                "role": "Delivery lead",
                "summary": "Coordinated cross-functional product delivery",
                "status": "CURRENT",
                "start_date": "2021-01",
                "end_date": None,
                "verification_status": "UNVERIFIED",
                "responsibilities": ["Coordinate delivery"],
                "leadership_responsibilities": ["Coach product and engineering teams"],
                "team_size": 4,
                "teams": ["Product and engineering"],
                "stakeholders": ["Operations"],
                "customers": ["Business customers"],
                "decisions": ["Prioritize reliability"],
                "outcomes": [{"value": None, "verification_status": "UNVERIFIED"}],
                "results": [{"value": None, "verification_status": "UNVERIFIED"}],
                "evidence": [{"source": "candidate_statement"}],
            }],
            "skills_tools": [{
                "name": "Product discovery skill",
                "title": "Product discovery",
                "role": "Practitioner",
                "summary": "Research and validate user problems",
                "status": "CURRENT",
                "start_date": "2018-01",
                "end_date": None,
                "verification_status": "UNVERIFIED",
                "skill": "Product discovery",
                "tool": "Jira",
                "technology": "REST APIs",
                "skills": ["Product discovery", "Requirements analysis"],
                "tools": ["Jira"],
                "technologies": ["REST APIs"],
                "proficiency": "ADVANCED",
                "years_experience": 8,
                "last_used": "2026-10",
            }],
            "education": [{
                "name": "Bachelor degree",
                "title": "BSc Mass Communication",
                "role": "Student",
                "summary": "Undergraduate degree",
                "verification_status": "UNVERIFIED",
                "institution": "Example University",
                "school": "Example University",
                "qualification": "BSc",
                "degree": "Bachelor of Science",
                "field": "Mass Communication",
                "start_date": "2000",
                "end_date": "2004",
                "graduation_date": "2004-06",
                "grade": None,
                "status": "COMPLETED",
                "evidence": [{"source": "candidate_statement"}],
            }],
            "certifications": [{
                "name": "Product certification record",
                "title": "Product certification",
                "role": "Credential holder",
                "summary": "Professional product certification",
                "start_date": "2024-01",
                "end_date": None,
                "verification_status": "UNVERIFIED",
                "certification": "Product certification",
                "qualification": "Professional product credential",
                "issuing_organization": "Example Institute",
                "credential_id": "CERT-001",
                "credential_url": "https://example.test/certificates/001",
                "issue_date": "2024-01",
                "expiry_date": None,
                "status": "ACTIVE",
                "evidence": [{"source": "candidate_statement"}],
            }],
        },
    }

    try:
        response = client.patch("/api/v1/candidate/profile", json=payload)
        assert response.status_code == 200, response.text
        saved = response.json()["data"]
        assert saved["revision"] == 2
        data = saved["profile_data"]
        # Every supplied PRD §6 field group must survive validation, persistence,
        # and API read-back without inferred or dropped values.
        for section, expected_fields in payload["sections"].items():
            assert data[section] == expected_fields
        assert data["career_history"][0]["responsibilities"] == ["Own product delivery"]
        assert data["career_history"][0]["stakeholders"] == ["Engineering", "Operations"]
        assert data["projects"][0]["product_status"] == "prototype"
        assert data["achievements"][0]["context"] == "Requests were handled manually"
        assert data["achievements"][0]["action"] == "Introduced a triage workflow"
        assert data["achievements"][0]["source"] == {"kind": "candidate_statement"}
        assert data["achievements"][0]["outcome"] is None
        assert data["achievements"][0]["metrics"] is None
        assert data["achievements"][0]["verification_status"] == "UNVERIFIED"
        assert data["education"][0]["end_date"] == "2004"
        assert data["certifications"][0]["credential_id"] == "CERT-001"

        # Clearing one repeatable section must not clear unrelated profile facts.
        cleared = client.patch(
            "/api/v1/candidate/profile",
            json={"expected_revision": 2, "sections": {"career_history": []}},
        )
        assert cleared.status_code == 200, cleared.text
        cleared_data = cleared.json()["data"]
        assert cleared_data["revision"] == 3
        assert cleared_data["profile_data"]["career_history"] == []
        assert cleared_data["profile_data"]["projects"][0]["name"] == "Workflow rebuild"
        assert cleared_data["profile_data"]["achievements"][0]["outcome"] is None

        stale = client.patch(
            "/api/v1/candidate/profile",
            json={"expected_revision": 2, "sections": {"identity": {"full_name": "Stale update"}}},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

