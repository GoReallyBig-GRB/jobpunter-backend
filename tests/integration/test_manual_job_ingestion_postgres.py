import os
from datetime import datetime, timezone
from uuid import UUID, uuid4
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.models import (
    AcquisitionRun, Job, JobDescriptionVersion, JobDiscoveryEvent, JobProvenance,
    RawSourceRecord, Source, User,
)
from app.infrastructure.db.models.target_policy import ForbiddenTargetRule
from app.infrastructure.db.session import get_db
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
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def make_client(db, user):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    connection = db.connection()

    def override_db():
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def make_user(db):
    user = User(display_name=f"Manual ingestion test {uuid4()}")
    db.add(user)
    db.flush()
    return user


def test_manual_jd_uses_shared_canonical_pipeline_and_replays_idempotently(db):
    user = make_user(db)
    client = make_client(db, user)
    payload = {
        "channel": "MANUAL_JD",
        "title": "Technical Product Manager",
        "company": "Example Systems",
        "description": (
            "Responsibilities include leading roadmap planning, coordinating engineering delivery, "
            "defining requirements, managing stakeholders, and improving product workflows. "
            "Qualifications include product management experience, strong communication skills, "
            "technical fluency, and the ability to analyze customer needs and prioritize delivery."
        ),
        "application_url": "https://jobs.example.com/roles/123?utm_source=email",
        "external_job_id": "example-role-123",
        "location": "Remote, Nigeria",
        "application_fee_amount": 25,
        "application_fee_currency": "USD",
        "relocation_required": True,
        "application_route_type": "FREE_ATS",
        "posting_date": "2026-10-01T00:00:00Z",
    }
    key = f"manual-jd-{uuid4()}"
    try:
        first = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert first.status_code == 201, first.text
        first_data = first.json()["data"]
        assert first_data["acquisition_status"] == "SUCCEEDED"
        assert first_data["canonicalization"]["status"] == "CREATED"
        assert first_data["canonicalization"]["replayed"] is False

        db.expire_all()
        job_id = UUID(first_data["canonicalization"]["job_id"])
        job = db.get(Job, job_id)
        assert job is not None
        assert job.canonical_title == "Technical Product Manager"
        assert job.application_url == "https://jobs.example.com/roles/123"
        assert job.metadata_json["external_job_id"] == "example-role-123"
        assert job.metadata_json["posting_date"] == "2026-10-01T00:00:00Z"
        assert job.metadata_json["discovery_method"] == "MANUAL_JD"
        assert job.metadata_json["application_fee_amount"] == 25.0
        assert job.metadata_json["application_fee_currency"] == "USD"
        assert job.metadata_json["relocation_required"] is True
        assert job.metadata_json["application_route_type"] == "FREE_ATS"
        assert db.scalar(select(func.count()).select_from(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == job_id
        )) == 1
        assert db.scalar(select(func.count()).select_from(JobProvenance).where(
            JobProvenance.job_id == job_id
        )) == 1

        replay = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert replay.status_code == 200, replay.text
        replay_data = replay.json()["data"]
        assert replay_data["idempotent_replay"] is True
        assert replay_data["canonicalization"]["replayed"] is True
        assert replay_data["canonicalization"]["job_id"] == str(job_id)
        assert db.scalar(select(func.count()).select_from(Job).where(Job.id == job_id)) == 1
        assert db.scalar(select(func.count()).select_from(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == job_id
        )) == 1
    finally:
        client.close()


def test_manual_url_retrieval_failure_returns_truthful_pasted_text_fallback(db, monkeypatch):
    from app.application.jobs.manual_url_retrieval import JobUrlRetrievalError
    monkeypatch.setattr(
        "app.api.v1.manual_ingestion.retrieve_job_posting",
        lambda url, **kwargs: (_ for _ in ()).throw(JobUrlRetrievalError("POSTING_CONTENT_INCOMPLETE")),
    )
    user = make_user(db)
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"url-only-{uuid4()}"},
            json={"channel": "MANUAL_URL", "source_url": "https://jobs.example.com/123"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "URL_RETRIEVAL_FAILED"
        assert detail["retrieval_status"] == "FAILED"
        assert detail["failure_category"] == "POSTING_CONTENT_INCOMPLETE"
        assert detail["pasted_text_fallback_available"] is True
    finally:
        client.close()


def test_idempotency_key_cannot_be_reused_for_different_job_content(db):
    user = make_user(db)
    client = make_client(db, user)
    key = f"manual-conflict-{uuid4()}"
    payload = {
        "channel": "MANUAL_JD",
        "title": "Product Manager",
        "company": "Example Systems",
        "description": "Responsibilities include planning and coordinating product delivery. Qualifications include relevant product experience and strong communication skills.",
    }
    try:
        first = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert first.status_code == 201, first.text
        changed = {**payload, "title": "Senior Product Manager"}
        conflict = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=changed,
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    finally:
        client.close()


def test_manual_url_retrieval_success_uses_shared_canonical_pipeline(db, monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.manual_ingestion.retrieve_job_posting",
        lambda url, **kwargs: {
            "title": "Technical Product Manager",
            "company": "Example Systems",
            "description": (
                "Responsibilities include leading roadmap planning, coordinating engineering delivery, "
                "defining requirements, managing stakeholders, and improving product workflows. "
                "Qualifications include product management experience, strong communication skills, "
                "technical fluency, and the ability to analyze customer needs and prioritize delivery."
            ),
            "location": "Remote, Nigeria",
            "retrieval_method": "PUBLIC_HTTP_JOB_POSTING",
            "retrieval_final_url": "https://jobs.example.com/roles/123",
            "retrieved_at": "2026-10-15T19:00:00+00:00",
        },
    )
    user = make_user(db)
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"manual-url-{uuid4()}"},
            json={"channel": "MANUAL_URL", "source_url": "https://jobs.example.com/roles/123"},
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert data["acquisition_status"] == "SUCCEEDED"
        job = db.get(Job, UUID(data["canonicalization"]["job_id"]))
        assert job is not None
        assert job.canonical_title == "Technical Product Manager"
        assert job.application_url is None  # A posting page is not an application URL.
        raw_record = db.scalar(select(RawSourceRecord).where(
            RawSourceRecord.acquisition_run_id == UUID(data["acquisition_run_id"])
        ))
        assert raw_record is not None
        assert raw_record.source_url == "https://jobs.example.com/roles/123"
        assert raw_record.raw_content["url_retrieval"]["status"] == "SUCCEEDED"
        assert raw_record.raw_content["url_retrieval"]["method"] == "PUBLIC_HTTP_JOB_POSTING"
        assert raw_record.raw_content["url_retrieval"]["final_url"] == "https://jobs.example.com/roles/123"
    finally:
        client.close()



def test_manual_url_pasted_details_fallback_skips_network_fetch(db, monkeypatch):
    def unexpected_fetch(_url):
        raise AssertionError("complete pasted details must not trigger network retrieval")

    monkeypatch.setattr("app.api.v1.manual_ingestion.retrieve_job_posting", unexpected_fetch)
    user = make_user(db)
    client = make_client(db, user)
    payload = {
        "channel": "MANUAL_URL",
        "source_url": "https://jobs.example.com/roles/123",
        "title": "Technical Product Manager",
        "company": "Example Systems",
        "description": (
            "Responsibilities include leading roadmap planning, coordinating engineering delivery, "
            "defining requirements, managing stakeholders, and improving product workflows. "
            "Qualifications include product management experience, strong communication skills, "
            "technical fluency, and the ability to analyze customer needs and prioritize delivery."
        ),
    }
    try:
        response = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"manual-url-fallback-{uuid4()}"},
            json=payload,
        )
        assert response.status_code == 201, response.text
        raw_record = db.scalar(select(RawSourceRecord).where(
            RawSourceRecord.acquisition_run_id == UUID(response.json()["data"]["acquisition_run_id"])
        ))
        assert raw_record.raw_content["url_retrieval"]["status"] == "NOT_ATTEMPTED"
        assert raw_record.raw_content["url_retrieval"]["method"] == "PASTED_TEXT_FALLBACK"
        assert raw_record.source_url == "https://jobs.example.com/roles/123"
        job_id = UUID(response.json()["data"]["canonicalization"]["job_id"])
        job = db.get(Job, job_id)
        assert job is not None
        assert job.application_url is None
    finally:
        client.close()

def test_manual_repeat_submission_records_unchanged_observation_and_replays(db):
    user = make_user(db)
    client = make_client(db, user)
    payload = {
        "channel": "MANUAL_JD",
        "title": "Technical Product Manager",
        "company": "Observation Systems",
        "description": (
            "Responsibilities include leading product roadmaps, coordinating engineering delivery, "
            "writing technical requirements, managing stakeholders, and improving product workflows. "
            "Qualifications include product management experience, technical fluency, and strong communication."
        ),
        "application_url": "https://jobs.example.com/observation-role",
        "external_job_id": "observation-role-1",
        "location": "Remote, Nigeria",
    }
    first_key = f"manual-observation-first-{uuid4()}"
    second_key = f"manual-observation-repeat-{uuid4()}"
    try:
        first = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": first_key},
            json=payload,
        )
        assert first.status_code == 201, first.text
        first_data = first.json()["data"]
        assert first_data["observed_count"] == 1
        assert first_data["new_count"] == 1
        job_id = UUID(first_data["canonicalization"]["job_id"])

        second = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": second_key},
            json=payload,
        )
        assert second.status_code == 201, second.text
        second_data = second.json()["data"]
        assert second_data["canonicalization"]["job_id"] == str(job_id)
        assert second_data["persisted_count"] == 0
        assert second_data["duplicate_count"] == 1
        assert second_data["observed_count"] == 1
        assert second_data["unchanged_count"] == 1

        db.expire_all()
        second_run = db.scalar(select(AcquisitionRun).where(AcquisitionRun.run_key.like(f"%{second_key}")))
        assert second_run is not None
        second_event = db.scalar(select(JobDiscoveryEvent).where(
            JobDiscoveryEvent.acquisition_run_id == second_run.id,
        ))
        assert second_event is not None
        assert second_event.observation_outcome == "UNCHANGED"
        assert second_event.canonical_job_id == job_id
        assert second_event.processing_status == "CANONICALIZED"

        replay = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": second_key},
            json=payload,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["data"]["idempotent_replay"] is True
        assert replay.json()["data"]["canonicalization"]["job_id"] == str(job_id)
        assert db.scalar(select(func.count()).select_from(JobDiscoveryEvent).where(
            JobDiscoveryEvent.acquisition_run_id == second_run.id,
        )) == 1
        assert db.scalar(select(func.count()).select_from(RawSourceRecord).where(
            RawSourceRecord.source_id == second_event.source_id,
        )) == 1
    finally:
        client.close()

def test_manual_email_digest_ingests_jobs_and_replays_by_message_identity(db):
    user = make_user(db)
    client = make_client(db, user)
    message_id = f"provider-message-{uuid4()}"
    payload = {
        "channel": "MANUAL_EMAIL",
        "email_subject": "2 new product jobs",
        "email_sender": "alerts@example.com",
        "email_message_id": message_id,
        "email_body": (
            "###\nTitle: Product Manager\nCompany: Acme Systems\nLocation: Remote\n"
            + "Responsibilities include roadmap planning, stakeholder alignment, requirements, and delivery. " * 3
            + "Qualifications include product ownership experience, communication skills, and technical fluency. "
            + "\nhttps://jobs.example.com/acme-pm\n"
            + "###\nTitle: Technical Product Owner\nCompany: Beta Works\nLocation: Nigeria\n"
            + "Responsibilities include backlog ownership, sprint planning, technical discovery, and delivery. " * 3
            + "Qualifications include technical product ownership experience and clear stakeholder communication. "
            + "\nhttps://jobs.example.com/beta-po\n"
            + "###\nTitle: Product Manager\nCompany: Acme Systems\nLocation: Remote\n"
            + "Responsibilities include roadmap planning, stakeholder alignment, requirements, and delivery. " * 3
            + "Qualifications include product ownership experience, communication skills, and technical fluency. "
            + "\nhttps://jobs.example.com/acme-pm\n"
        ),
    }
    key = f"manual-email-{uuid4()}"
    try:
        first = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert first.status_code == 201, first.text
        first_data = first.json()["data"]
        assert first_data["acquisition_status"] == "SUCCEEDED"
        assert first_data["processed_job_count"] == 2
        assert first_data["review_required_count"] == 0
        assert len(first_data["canonicalizations"]) == 2

        raw_records = db.scalars(
            select(RawSourceRecord).where(
                RawSourceRecord.acquisition_run_id == UUID(first_data["acquisition_run_id"])
            ).order_by(RawSourceRecord.id)
        ).all()
        assert len(raw_records) == 2
        assert {record.raw_content["email_message_id"] for record in raw_records} == {message_id}
        assert all(record.raw_content["application_url"] is None for record in raw_records)
        assert all(record.raw_content["discovery_method"] == "MANUAL_EMAIL" for record in raw_records)

        replay = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert replay.status_code == 200, replay.text
        replay_data = replay.json()["data"]
        assert replay_data["idempotent_replay"] is True
        assert replay_data["email_message_id"] == message_id
        assert len(replay_data["canonicalizations"]) == 2
        assert db.scalar(select(func.count()).select_from(RawSourceRecord).where(
            RawSourceRecord.acquisition_run_id == UUID(first_data["acquisition_run_id"])
        )) == 2
    finally:
        client.close()


def test_changed_email_alert_creates_new_observation_and_jd_version(db):
    user = make_user(db)
    client = make_client(db, user)
    first_payload = {
        "channel": "MANUAL_EMAIL",
        "email_subject": "Product Manager alert",
        "email_sender": "alerts@example.com",
        "email_message_id": f"provider-message-{uuid4()}",
        "email_body": (
            "Title: Product Manager\nCompany: Acme Systems\nLocation: Remote\n"
            + "Responsibilities include roadmap planning, stakeholder alignment, requirements, and delivery. " * 3
            + "Qualifications include product management experience, communication skills, and technical fluency.\n"
            + "https://jobs.example.com/acme-pm"
        ),
    }
    try:
        first = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"email-first-{uuid4()}"},
            json=first_payload,
        )
        assert first.status_code == 201, first.text
        first_data = first.json()["data"]
        job_id = UUID(first_data["canonicalization"]["job_id"])

        changed_payload = {
            **first_payload,
            "email_message_id": f"provider-message-{uuid4()}",
            "email_body": (
                "Title: Product Manager\nCompany: Acme Systems\nLocation: Remote\n"
                + "Responsibilities include roadmap ownership, cross-functional planning, delivery tracking, and product discovery. " * 3
                + "Qualifications include product strategy experience, technical fluency, and clear stakeholder communication.\n"
                + "https://jobs.example.com/acme-pm"
            ),
        }
        changed = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"email-changed-{uuid4()}"},
            json=changed_payload,
        )
        assert changed.status_code == 201, changed.text
        changed_data = changed.json()["data"]
        assert changed_data["canonicalization"]["job_id"] == str(job_id)
        assert changed_data["canonicalization"]["status"] == "MATCHED"
        assert db.scalar(select(func.count()).select_from(Job).where(Job.id == job_id)) == 1
        assert db.scalar(select(func.count()).select_from(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == job_id
        )) == 2
        assert db.scalar(select(func.count()).select_from(JobProvenance).where(
            JobProvenance.job_id == job_id
        )) == 2
    finally:
        client.close()


def test_manual_url_ingestion_enforces_persisted_global_deny_rule_before_network(db, monkeypatch):
    user = make_user(db)
    db.add(ForbiddenTargetRule(
        rule_type="HOST_EXACT",
        pattern="jobs.example.com",
        connector_scope="ALL",
        reason="Blocked by global target policy",
        active=True,
        created_by_user_id=user.id,
    ))
    db.flush()
    monkeypatch.setattr(
        "app.application.jobs.manual_url_retrieval._fetch_once",
        lambda url: pytest.fail(f"forbidden URL must not be fetched: {url}"),
    )
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"manual-forbidden-url-{uuid4()}"},
            json={"channel": "MANUAL_URL", "source_url": "https://jobs.example.com/123"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "URL_RETRIEVAL_FAILED"
        assert detail["failure_category"] == "FORBIDDEN_TARGET_RULE_MATCHED"
    finally:
        client.close()

@pytest.mark.parametrize("redirect_status", [301, 302, 303, 307, 308])
def test_manual_url_refreshes_persisted_global_rules_before_redirect_fetch(
    db, monkeypatch, redirect_status
):
    user = make_user(db)
    calls = []

    def redirect_then_publish_rule(url):
        calls.append(url)
        if len(calls) == 1:
            db.add(ForbiddenTargetRule(
                rule_type="HOST_EXACT",
                pattern="blocked.example.com",
                connector_scope="ALL",
                reason="Rule published while manual retrieval was running",
                active=True,
                created_by_user_id=user.id,
            ))
            db.flush()
            return redirect_status, {"location": "https://blocked.example.com/123"}, b""
        pytest.fail(f"redirect destination must be denied before fetch: {url}")

    monkeypatch.setattr(
        "app.application.jobs.manual_url_retrieval._fetch_once",
        redirect_then_publish_rule,
    )
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"manual-live-rule-{redirect_status}-{uuid4()}"},
            json={
                "channel": "MANUAL_URL",
                "source_url": "https://jobs.example.com/123",
            },
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "URL_RETRIEVAL_FAILED"
        assert detail["failure_category"] == "FORBIDDEN_TARGET_RULE_MATCHED"
        assert calls == ["https://jobs.example.com/123"]
    finally:
        client.close()

def test_manual_url_observes_committed_global_rule_from_second_session(db, monkeypatch):
    engine = create_engine(os.environ["DATABASE_URL"])
    user_id = rule_id = None
    client = None
    calls = []
    try:
        with Session(engine) as seed_db:
            user = User(display_name=f"Manual live-policy test {uuid4()}")
            seed_db.add(user)
            seed_db.flush()
            user_id = user.id
            seed_db.commit()

        def redirect_after_concurrent_rule_commit(url):
            nonlocal rule_id
            calls.append(url)
            if len(calls) == 1:
                with Session(engine) as policy_db:
                    rule = ForbiddenTargetRule(
                        rule_type="HOST_EXACT",
                        pattern="blocked.example.com",
                        connector_scope="ALL",
                        reason="Committed while manual retrieval was running",
                        active=True,
                        created_by_user_id=user_id,
                    )
                    policy_db.add(rule)
                    policy_db.commit()
                    rule_id = rule.id
                return 302, {"location": "https://blocked.example.com/123"}, b""
            pytest.fail(f"committed deny rule must block redirect before fetch: {url}")

        monkeypatch.setattr(
            "app.application.jobs.manual_url_retrieval._fetch_once",
            redirect_after_concurrent_rule_commit,
        )
        # The user is committed independently so the concurrent policy transaction
        # can satisfy its foreign key without depending on the test's outer transaction.
        client = make_client(db, SimpleNamespace(id=user_id))
        response = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"manual-committed-rule-{uuid4()}"},
            json={
                "channel": "MANUAL_URL",
                "source_url": "https://jobs.example.com/123",
            },
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "URL_RETRIEVAL_FAILED"
        assert detail["failure_category"] == "FORBIDDEN_TARGET_RULE_MATCHED"
        assert calls == ["https://jobs.example.com/123"]
    finally:
        if client is not None:
            client.close()
        # Roll back API writes held by the fixture's outer transaction before
        # cleaning up independently committed policy fixtures.
        db.rollback()
        with Session(engine) as cleanup_db:
            if rule_id is not None:
                rule = cleanup_db.get(ForbiddenTargetRule, rule_id)
                if rule is not None:
                    cleanup_db.delete(rule)
            if user_id is not None:
                user = cleanup_db.get(User, user_id)
                if user is not None:
                    cleanup_db.delete(user)
            cleanup_db.commit()
        engine.dispose()


def test_manual_url_blocks_loopback_redirect_before_socket_connection(db, monkeypatch):
    from app.application.jobs import manual_url_retrieval as retrieval_module
    from app.infrastructure.db.models import AuditEvent

    user = make_user(db)
    calls = []
    source_url = "https://jobs.example.com/123?job_ref=private-source-value"
    blocked_url = "http://127.0.0.1/admin?ref=private-redirect-value"
    real_fetch_once = retrieval_module._fetch_once

    def redirect_to_loopback(url):
        calls.append(url)
        if len(calls) == 1:
            return 302, {"location": blocked_url}, b""
        return real_fetch_once(url)

    monkeypatch.setattr(retrieval_module, "_fetch_once", redirect_to_loopback)
    monkeypatch.setattr(
        retrieval_module.socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail(
            "loopback redirect must be blocked before socket connection"
        ),
    )
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"manual-loopback-redirect-{uuid4()}"},
            json={"channel": "MANUAL_URL", "source_url": source_url},
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "URL_RETRIEVAL_FAILED"
        assert detail["failure_category"] == "NON_PUBLIC_ADDRESS"
        assert calls == [source_url, blocked_url]

        audit_events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "MANUAL_URL_RETRIEVAL_FAILED",
                AuditEvent.actor_id == user.id,
            )
        ).all()
        assert len(audit_events) == 1
        audit_event = audit_events[0]
        assert audit_event.resource_type == "MANUAL_JOB_INGESTION"
        assert audit_event.to_status == "FAILED"
        assert audit_event.reason == "NON_PUBLIC_ADDRESS"
        assert audit_event.details == {
            "channel": "MANUAL_URL",
            "retrieval_status": "FAILED",
            "failure_category": "NON_PUBLIC_ADDRESS",
            "pasted_text_fallback_available": True,
        }
        audit_details = repr(audit_event.details)
        assert source_url not in audit_details
        assert blocked_url not in audit_details
        assert "private-source-value" not in audit_details
        assert "private-redirect-value" not in audit_details
    finally:
        client.close()
