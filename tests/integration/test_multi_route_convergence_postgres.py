"""Section F acceptance: one vacancy reached through several routes converges on one canonical job."""

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Job, JobProvenance, JobResolution, Source, SourceConnector
from app.infrastructure.db.repositories.canonicalization import SqlAlchemyCanonicalizationRepository
from tests.integration.test_canonicalization_postgres import seed_raw_record

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)

DESCRIPTION = "Own roadmap planning, delivery and stakeholder communication. " * 12
ROUTES = [
    ("GREENHOUSE", "https://boards.example.test/acme/jobs/777", "gh-777"),
    ("RSS_ATOM", "https://boards.example.test/acme/jobs/777?utm_source=feed&utm_medium=rss", "rss-guid-abc"),
    ("JSON_API", "https://boards.example.test/acme/jobs/777#apply", "api-id-9"),
]


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


def _route(db, connector_type):
    source = Source(name=f"{connector_type} route {uuid4()}", source_type=connector_type, status="ACTIVE")
    db.add(source)
    db.flush()
    connector = SourceConnector(source_id=source.id, connector_type=connector_type,
                                connector_version="1", status="ACTIVE")
    db.add(connector)
    db.flush()
    return source, connector


def test_same_vacancy_through_three_connector_routes_yields_one_canonical_job(db):
    repository = SqlAlchemyCanonicalizationRepository(db)
    job_ids, raw_ids, source_ids = [], [], []
    for connector_type, url, external_id in ROUTES:
        source, connector = _route(db, connector_type)
        raw = seed_raw_record(db, source=source, connector=connector, title="Technical Product Manager",
                              description=DESCRIPTION, url=url, external_id=external_id)
        result = repository.process_raw_record(raw.id)
        assert result["status"] in {"CREATED", "MATCHED"}, result
        job_ids.append(result["job_id"])
        raw_ids.append(raw.id)
        source_ids.append(source.id)

    assert len(set(job_ids)) == 1, f"routes produced separate jobs: {job_ids}"
    job_id = UUID(job_ids[0])
    assert db.scalar(select(func.count()).select_from(Job).where(Job.id.in_([UUID(j) for j in job_ids]))) == 1

    # every route's provenance and resolution is preserved against the single job
    provenance_sources = set(db.scalars(select(JobProvenance.source_id).where(JobProvenance.job_id == job_id)))
    assert provenance_sources == set(source_ids)
    resolutions = db.scalars(select(JobResolution).where(JobResolution.raw_source_record_id.in_(raw_ids))).all()
    assert len(resolutions) == 3
    assert {r.job_id for r in resolutions if r.job_id} == {job_id}

    # replaying every route is idempotent: no new job, no lost provenance
    for raw_id in raw_ids:
        assert repository.process_raw_record(raw_id)["job_id"] == job_ids[0]
    assert set(db.scalars(select(JobProvenance.source_id).where(JobProvenance.job_id == job_id))) == set(source_ids)


def test_a_different_vacancy_from_another_route_does_not_merge(db):
    repository = SqlAlchemyCanonicalizationRepository(db)
    results = []
    for (connector_type, url, external_id), title, desc in [
        (ROUTES[0], "Technical Product Manager", DESCRIPTION),
        (("RSS_ATOM", "https://boards.example.test/acme/jobs/888", "rss-guid-other"),
         "Senior Data Engineer", "Build and operate batch and streaming data pipelines. " * 12),
    ]:
        source, connector = _route(db, connector_type)
        raw = seed_raw_record(db, source=source, connector=connector, title=title,
                              description=desc, url=url, external_id=external_id)
        results.append(repository.process_raw_record(raw.id))
    assert results[0]["job_id"] != results[1]["job_id"]
