"""Lever and Ashby feeds, as JSON_API configurations, must produce complete canonical jobs.

Runs the real JsonApiConnector (fake HTTP client, public DNS mocked) through AcquisitionService,
the transactional outbox and canonicalization on PostgreSQL. Catches mapping defects that a
connector-only canary cannot (a Job with no title, company, location or description).
"""
import json
import os
import socket
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.common.outbox_dispatcher import (
    OutboxDispatcher,
    canonicalization_event_handler,
)
from app.application.jobs.service import AcquisitionService
from app.infrastructure.acquisition.json_api import JsonApiConnector
from app.infrastructure.db.models import (
    Job,
    JobDescriptionVersion,
    JobResolution,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
)
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="PostgreSQL integration test requires DATABASE_URL"
)

DESCRIPTION = (
    "Responsibilities include owning the product roadmap and delivery across several teams, "
    "working with engineering and design to plan releases, and reporting progress to leadership. "
    "Requirements: five years of product management experience, strong communication skills, "
    "and the ability to analyze data and make clear decisions under uncertainty."
)
LEVER = (
    {"endpoint_url": "https://api.lever.co/v0/postings/examplecorp", "base_params": {"mode": "json"},
     "field_mapping": {"external_id": "id", "title": "text", "job_url": "hostedUrl",
                       "application_url": "applyUrl", "location": "categories.location",
                       "description": "descriptionPlain"},
     "static_fields": {"company": "Example Corp"}},
    [{"id": "lv-1", "text": "Senior Product Manager", "hostedUrl": "https://jobs.lever.co/examplecorp/lv-1",
      "applyUrl": "https://jobs.lever.co/examplecorp/lv-1/apply",
      "categories": {"location": "Lagos"}, "descriptionPlain": DESCRIPTION}],
    "Senior Product Manager", "https://jobs.lever.co/examplecorp/lv-1/apply", "Lagos",
)
ASHBY = (
    {"endpoint_url": "https://api.ashbyhq.com/posting-api/job-board/examplecorp", "items_path": "jobs",
     "field_mapping": {"external_id": "id", "title": "title", "job_url": "jobUrl",
                       "application_url": "applyUrl", "location": "location",
                       "description": "descriptionPlain"},
     "static_fields": {"company": "Example Corp"}},
    {"jobs": [{"id": "ab-1", "title": "Data Engineer", "location": "Remote",
               "jobUrl": "https://jobs.ashbyhq.com/examplecorp/ab-1",
               "applyUrl": "https://jobs.ashbyhq.com/examplecorp/ab-1/application",
               "descriptionPlain": DESCRIPTION}]},
    "Data Engineer", "https://jobs.ashbyhq.com/examplecorp/ab-1/application", "Remote",
)


class FeedClient:
    def __init__(self, payload):
        self.payload = payload

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None, timeout=None, follow_redirects=None):
        yield httpx.Response(
            200, content=json.dumps(self.payload).encode(), request=httpx.Request("GET", str(url)),
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


def _run_feed(db, monkeypatch, config, payload):
    original = socket.getaddrinfo
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: original(host, port, *a, **k)
        if str(host).strip("[]").lower() in {"localhost", "127.0.0.1", "::1"}
        else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))],
    )
    source = Source(name=f"ats feed {uuid4()}", source_type="JSON_API", status="ACTIVE")
    db.add(source)
    db.flush()
    connector_row = SourceConnector(
        source_id=source.id, connector_type="JSON_API", connector_version="1.0.0", status="ACTIVE",
    )
    db.add(connector_row)
    db.flush()

    run_key = f"ats-{uuid4().hex}"
    result = AcquisitionService(
        SqlAlchemyAcquisitionRepository(db),
        JsonApiConnector(http_client=FeedClient(payload), allow_unsafe_http_client=True),
    ).run(source.id, connector_row.id, run_key, {**config, "acquisition_policy_revalidator": lambda **k: None})
    assert result.status == "SUCCEEDED" and result.acquired_count == 1 and result.persisted_count == 1

    db.expire_all()
    raw = db.scalar(select(RawSourceRecord).where(RawSourceRecord.source_id == source.id))
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == raw.id))
    assert event is not None and event.status == "PENDING"

    factory = sessionmaker(
        bind=db.connection(), class_=Session, join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    dispatcher = OutboxDispatcher(
        factory, {"RAW_SOURCE_RECORD_ACQUIRED": canonicalization_event_handler(factory)},
    )
    assert dispatcher.dispatch_batch(limit=10)["processed"] >= 1

    db.expire_all()
    resolution = db.scalar(select(JobResolution).where(JobResolution.raw_source_record_id == raw.id))
    assert resolution is not None and resolution.status == "CREATED"
    job = db.get(Job, resolution.job_id)
    description = db.scalar(select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job.id))
    return job, description, raw


@pytest.mark.parametrize("feed", [LEVER, ASHBY], ids=["lever", "ashby"])
def test_feed_reaches_a_complete_canonical_job(db, monkeypatch, feed):
    config, payload, title, apply_url, location = feed
    job, description, _ = _run_feed(db, monkeypatch, config, payload)
    assert job.canonical_title == title
    assert job.company_name == "Example Corp"
    assert job.application_url == apply_url
    assert job.original_location_text == location
    assert description.completeness_status == "USABLE"
    assert description.content == DESCRIPTION


def test_lever_posting_with_duties_in_lists_becomes_a_usable_job(db, monkeypatch):
    """Lever keeps duties and requirements outside descriptionPlain; they must still reach the job."""
    config = {
        **LEVER[0],
        "description_paths": ["descriptionPlain", "lists", "additionalPlain"],
        "omit_fields": ["description", "descriptionBody", "descriptionBodyPlain", "opening", "additional"],
    }
    payload = [{
        "id": "lv-9", "text": "Product Manager", "hostedUrl": "https://jobs.lever.co/examplecorp/lv-9",
        "applyUrl": "https://jobs.lever.co/examplecorp/lv-9/apply", "categories": {"location": "Lagos"},
        "description": "<div>Join our team and help us build payments across Africa today.</div>",
        "descriptionPlain": "Join our team and help us build payments across Africa today.",
        "lists": [
            {"text": "What you will do", "content": "<li>Own the roadmap and delivery for payments products</li>"
                                                     "<li>Work with engineering and design to plan releases</li>"
                                                     "<li>Report progress and risks to leadership every week</li>"},
            {"text": "Requirements", "content": "<li>Five years of product management experience</li>"
                                                  "<li>Strong communication skills and analytical ability</li>"
                                                  "<li>Ability to make clear decisions under uncertainty</li>"},
        ],
        "additionalPlain": "We are an equal opportunity employer.",
    }]
    job, description, raw = _run_feed(db, monkeypatch, config, payload)
    assert description.completeness_status == "USABLE"
    assert "Requirements" in description.content and "Five years of product management" in description.content
    assert "description" in raw.raw_content and "descriptionBody" not in raw.raw_content
    assert job.company_name == "Example Corp"
