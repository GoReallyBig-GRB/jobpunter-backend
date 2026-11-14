"""Real-session publication races and legacy general-schedule cutover."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.v1 import admin_discovery as api
from app.application.jobs.discovery_orchestration import strategy_configuration_hash
from app.infrastructure.db.models import (
    AdminGrant,
    DiscoverySchedule,
    DiscoveryStrategy,
    DiscoveryStrategyVersion,
    GovernanceConfigVersion,
    User,
)
from app.ports.identity import AuthenticatedPrincipal
from tests.integration.test_admin_discovery_schedules_postgres import _schedule_payload, db  # noqa: F401
from tests.integration.test_admin_discovery_strategies_postgres import _payload

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="PostgreSQL required")


def test_general_cutover_pauses_legacy_without_inventing_publication(db):  # noqa: F811
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0085_discovery_schedule_governance.py"
    spec = importlib.util.spec_from_file_location("schedule_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    for statement in (
        "CREATE TEMP TABLE governance_config_versions (id uuid PRIMARY KEY, config_key text)",
        "CREATE TEMP TABLE discovery_schedules (id uuid PRIMARY KEY, status text, revision int, next_run_at timestamptz, frequency_definition json)",
        "CREATE TEMP TABLE audit_events (id uuid PRIMARY KEY, event_type text, resource_type text, resource_id uuid, actor_type text, from_status text, to_status text, reason text, details json, occurred_at timestamptz)",
    ):
        db.execute(text(statement))
    db.execute(text("INSERT INTO discovery_schedules VALUES (:id, 'ACTIVE', 5, now(), CAST(:payload AS json))"), {"id": uuid4(), "payload": '{"interval_minutes":60}'})
    with Operations.context(MigrationContext.configure(db.connection())):
        migration.upgrade()
    row = db.execute(text("SELECT * FROM discovery_schedules")).mappings().one()
    assert row["status"] == "PAUSED" and row["revision"] == 6 and row["next_run_at"] is None
    assert row["config_version_id"] is None and row["frequency_definition"] == {"interval_minutes": 60}
    event = db.execute(text("SELECT * FROM audit_events")).mappings().one()
    assert event["actor_type"] == "SYSTEM" and event["details"]["previous_revision"] == 5
    assert db.scalar(text("SELECT count(*) FROM governance_config_versions")) == 0
    with Operations.context(MigrationContext.configure(db.connection())):
        migration.downgrade()
        migration.upgrade()
    assert db.scalar(text("SELECT count(*) FROM audit_events")) == 1
    db.execute(text("INSERT INTO governance_config_versions VALUES (:id, :key)"), {"id": uuid4(), "key": f"discovery.schedule.{row['id']}"})
    with (pytest.raises(DBAPIError), db.begin_nested(), Operations.context(MigrationContext.configure(db.connection()))):
        migration.downgrade()


@pytest.mark.parametrize("same_draft", [False, True])
def test_general_concurrent_publication_has_one_winner(same_draft):
    engine = create_engine(os.environ["DATABASE_URL"])
    actor_id = schedule_id = None

    def request(path, method="POST"):
        return Request({"type": "http", "method": method, "path": path,
                        "route": SimpleNamespace(path=path), "headers": []})

    try:
        with Session(engine) as seed:
            actor = User(display_name=f"Concurrent general schedule {uuid4()}")
            seed.add(actor)
            seed.flush()
            actor_id = actor.id
            seed.add(AdminGrant(user_id=actor_id, role="OPERATIONS", active=True))
            strategy = DiscoveryStrategy(strategy_key=f"race.{uuid4().hex}", name="Concurrent", purpose="Test", status="ENABLED", created_by_user_id=actor_id)
            seed.add(strategy)
            seed.flush()
            payload = _payload()
            version = DiscoveryStrategyVersion(strategy_id=strategy.id, version=1, payload=payload,
                configuration_hash=strategy_configuration_hash(payload), schema_version="v1", status="PUBLISHED",
                validation_status="VALID", validation_result={"valid": True}, created_by_user_id=actor_id,
                published_by_user_id=actor_id, published_at=datetime.now(UTC))
            seed.add(version)
            seed.flush()
            principal = AuthenticatedPrincipal(user_id=actor_id, auth_provider="test")
            created = api.create_schedule(api.ScheduleCreateRequest.model_validate(_schedule_payload(version.id, activate=False)),
                request("/api/v1/admin/discovery/schedules"), principal, seed)["data"]
            schedule_id = UUID(str(created["id"]))
            first = UUID(str(created["draft_config_version_id"]))
            draft_payload = _schedule_payload(version.id, activate=False)
            draft_payload.pop("activate")
            second = first if same_draft else UUID(str(api.create_discovery_schedule_draft(schedule_id,
                api.GovernedDiscoveryScheduleConfiguration.model_validate(draft_payload),
                request("/api/v1/admin/discovery/schedules/{schedule_id}/versions"), principal, seed)["data"]["id"]))
        gate = Barrier(2)

        def publish(config_id):
            with Session(engine) as session:
                gate.wait(timeout=15)
                try:
                    api.publish_discovery_schedule_version(schedule_id, config_id,
                        request("/api/v1/admin/discovery/schedules/{schedule_id}/versions/{config_id}/publish"), principal, session)
                    return 200
                except HTTPException as exc:
                    return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(publish, first), pool.submit(publish, second)]
            assert sorted(f.result(timeout=30) for f in futures) == [200, 409]
        with Session(engine) as verify:
            versions = list(verify.scalars(select(GovernanceConfigVersion).where(
                GovernanceConfigVersion.config_key == f"discovery.schedule.{schedule_id}", GovernanceConfigVersion.status == "PUBLISHED")))
            assert len(versions) == 1
            schedule = verify.get(DiscoverySchedule, schedule_id)
            assert schedule.config_version_id == versions[0].id and schedule.status == "PAUSED"
    finally:
        with Session(engine) as cleanup:
            if actor_id:
                cleanup.execute(update(AdminGrant).where(AdminGrant.user_id == actor_id).values(active=False))
            cleanup.commit()
        engine.dispose()
