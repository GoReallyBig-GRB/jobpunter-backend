"""Concurrent publication and truthful migration cutover for source refresh governance."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_admin_governance_postgres import db  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="PostgreSQL required")


def _migration():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0084_source_refresh_governance.py"
    spec = importlib.util.spec_from_file_location("refresh_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_requires_drain_pauses_legacy_and_preserves_settings(db):  # noqa: F811
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    # Temporary legacy tables shadow public tables on this connection only.
    # This exercises the real migration without altering the test suite schema.
    statements = [
        "CREATE TEMP TABLE governance_config_versions (id uuid PRIMARY KEY, config_key text)",
        ("CREATE TEMP TABLE source_refresh_schedules (id uuid PRIMARY KEY, status text, revision int, "
        "interval_minutes int, window_start_minute int, window_end_minute int, window_timezone text, status_reason text)"),
        ("CREATE TEMP TABLE source_refresh_schedule_events (id uuid PRIMARY KEY, schedule_id uuid, "
        "schedule_revision int, action text, reason text, before json, after json, created_at timestamptz, actor_user_id uuid)"),
        ("CREATE TEMP TABLE work_items (id uuid PRIMARY KEY, work_type text, status text, "
        "completed_at timestamptz, revision int, result json)"),
    ]
    for statement in statements:
        db.execute(text(statement))
    schedule_id, item_id = uuid4(), uuid4()
    db.execute(text("INSERT INTO source_refresh_schedules VALUES (:id, 'ACTIVE', 3, 60, 1320, 120, 'UTC', NULL)"), {"id": schedule_id})
    db.execute(text("INSERT INTO work_items VALUES (:id, 'SOURCE_REFRESH', 'PROCESSING', NULL, 1, NULL)"), {"id": item_id})
    migration = _migration()
    with (pytest.raises(DBAPIError), db.begin_nested(),
          Operations.context(MigrationContext.configure(db.connection()))):
        migration.upgrade()
    db.execute(text("UPDATE work_items SET status = 'PENDING'"))
    with Operations.context(MigrationContext.configure(db.connection())):
        migration.upgrade()
    row = db.execute(text("SELECT * FROM source_refresh_schedules")).mappings().one()
    assert row["status"] == "PAUSED" and row["revision"] == 4
    assert (row["interval_minutes"], row["window_start_minute"], row["window_end_minute"], row["window_timezone"]) == (60, 1320, 120, "UTC")
    event = db.execute(text("SELECT * FROM source_refresh_schedule_events")).mappings().one()
    assert event["action"] == "MIGRATED" and event["actor_user_id"] is None
    assert event["before"]["status"] == "ACTIVE" and event["after"]["status"] == "PAUSED"
    assert db.scalar(text("SELECT count(*) FROM governance_config_versions")) == 0
    item = db.execute(text("SELECT status, result FROM work_items")).mappings().one()
    assert item["status"] == "CANCELLED" and item["result"]["reason"] == "GOVERNANCE_CUTOVER"
    with Operations.context(MigrationContext.configure(db.connection())):
        migration.downgrade()
        migration.upgrade()
    assert db.scalar(text("SELECT count(*) FROM source_refresh_schedule_events")) == 1


@pytest.mark.parametrize("same_draft", [False, True])
def test_concurrent_publications_have_one_winner(same_draft):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    from fastapi import HTTPException, Request
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.api.v1 import admin
    from app.infrastructure.db.models import (
        AdminGrant,
        GovernanceConfigVersion,
        Source,
        SourceRefreshSchedule,
        User,
    )
    from app.ports.identity import AuthenticatedPrincipal
    from tests.integration.test_admin_source_refresh_schedules_postgres import _source

    engine = create_engine(os.environ["DATABASE_URL"])
    source_id = actor_id = None
    try:
        with Session(engine) as seed:
            actor = User(display_name=f"Concurrent schedule operator {uuid4()}")
            seed.add(actor)
            seed.flush()
            actor_id = actor.id
            seed.add(AdminGrant(user_id=actor_id, role="OPERATIONS", active=True))
            _, source, _ = _source(seed)
            source_id = source.id
            seed.commit()
            principal = AuthenticatedPrincipal(user_id=actor_id, auth_provider="test")
            request = Request({"type": "http", "method": "PUT", "path": "/api/v1/admin/discovery/source/schedule",
                               "route": SimpleNamespace(path="/api/v1/admin/discovery/sources/{source_id}/schedule"), "headers": []})
            first = admin.set_discovery_source_schedule(source_id, admin.DiscoveryScheduleRequest(
                interval_minutes=60, reason="first concurrent draft"), request, principal, seed)["data"]["id"]
            second = first if same_draft else admin.set_discovery_source_schedule(source_id, admin.DiscoveryScheduleRequest(
                interval_minutes=120, reason="second concurrent draft"), request, principal, seed)["data"]["id"]
        gate = Barrier(2)

        def publish(config_id):
            with Session(engine) as session:
                request = Request({"type": "http", "method": "POST", "path": "/api/v1/admin/discovery/source/schedule/publish",
                                   "route": SimpleNamespace(path="/api/v1/admin/discovery/sources/{source_id}/schedule/versions/{config_id}/publish"),
                                   "headers": [(b"x-request-id", str(uuid4()).encode())]})
                gate.wait(timeout=15)
                try:
                    admin.publish_source_refresh_version(source_id, config_id, request, principal, session)
                    return 200
                except HTTPException as exc:
                    return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(publish, first), pool.submit(publish, second)]
            assert sorted(f.result(timeout=30) for f in futures) == [200, 409]
        with Session(engine) as verify:
            versions = list(verify.scalars(select(GovernanceConfigVersion).where(
                GovernanceConfigVersion.config_key == f"discovery.source_refresh.{source_id}",
                GovernanceConfigVersion.status == "PUBLISHED",
            )))
            assert len(versions) == 1
            schedule = verify.scalar(select(SourceRefreshSchedule).where(SourceRefreshSchedule.source_id == source_id))
            assert schedule.config_version_id == versions[0].id
            assert schedule.interval_minutes == versions[0].payload["interval_minutes"]
    finally:
        # Immutable configuration/history retains its actor and source provenance.
        with Session(engine) as cleanup:
            if source_id:
                cleanup.execute(update(Source).where(Source.id == source_id).values(status="PAUSED"))
                cleanup.execute(update(SourceRefreshSchedule).where(SourceRefreshSchedule.source_id == source_id).values(status="PAUSED"))
            if actor_id:
                cleanup.execute(update(AdminGrant).where(AdminGrant.user_id == actor_id).values(active=False))
            cleanup.commit()
        engine.dispose()
