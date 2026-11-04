import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.v1 import router as router_module
from app.config.settings import get_settings
from app.infrastructure.db.schema_revision import CURRENT_SCHEMA_REVISION


def _database(revision: str):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": revision},
        )
    return engine


def test_packaged_schema_revision_matches_single_alembic_head():
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == [CURRENT_SCHEMA_REVISION]


def test_liveness_reports_injected_deployment_revision(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_DEPLOYMENT_REVISION", "a" * 40)
    get_settings.cache_clear()
    try:
        assert router_module.health() == {
            "data": {"status": "ok", "deployment_revision": "a" * 40}
        }
    finally:
        get_settings.cache_clear()


def test_readiness_requires_matching_database_head_and_revision(monkeypatch):
    revision = "0081_run_budgets_and_snapshots"
    engine = _database(revision)
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "staging")
    monkeypatch.setenv("JOBPUNTER_DEPLOYMENT_REVISION", "b" * 40)
    monkeypatch.setattr(router_module, "_code_migration_heads", lambda: (revision,))
    get_settings.cache_clear()

    try:
        with Session(engine) as session:
            response = router_module.readiness(session)
        assert response.status_code == 200
        assert json.loads(response.body)["data"] == {
            "status": "ready",
            "environment": "staging",
            "deployment_revision": "b" * 40,
            "checks": {
                "database": True,
                "schema_current": True,
                "deployment_revision_configured": True,
            },
            "schema": {
                "database_revisions": [revision],
                "code_heads": [revision],
            },
        }
    finally:
        get_settings.cache_clear()
        engine.dispose()


def test_readiness_fails_closed_for_schema_drift(monkeypatch):
    engine = _database("0080_source_refresh_windows_history")
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "staging")
    monkeypatch.setenv("JOBPUNTER_DEPLOYMENT_REVISION", "c" * 40)
    monkeypatch.setattr(
        router_module, "_code_migration_heads", lambda: ("0081_run_budgets_and_snapshots",),
    )
    get_settings.cache_clear()

    try:
        with Session(engine) as session:
            response = router_module.readiness(session)
        assert response.status_code == 503
        payload = json.loads(response.body)
        assert payload["data"]["status"] == "not_ready"
        assert payload["data"]["checks"]["schema_current"] is False
    finally:
        get_settings.cache_clear()
        engine.dispose()


def test_readiness_requires_deployment_revision_outside_development(monkeypatch):
    revision = "0081_run_budgets_and_snapshots"
    engine = _database(revision)
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "staging")
    monkeypatch.delenv("JOBPUNTER_DEPLOYMENT_REVISION", raising=False)
    monkeypatch.setattr(router_module, "_code_migration_heads", lambda: (revision,))
    get_settings.cache_clear()
    try:
        with Session(engine) as session:
            response = router_module.readiness(session)
        payload = json.loads(response.body)
        assert response.status_code == 503
        assert payload["data"]["checks"] == {
            "database": True,
            "schema_current": True,
            "deployment_revision_configured": False,
        }
    finally:
        get_settings.cache_clear()
        engine.dispose()


def test_readiness_hides_database_failure_details(monkeypatch):
    class BrokenSession:
        def execute(self, statement):
            raise RuntimeError("postgresql://secret-host:5432/private")

    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "staging")
    monkeypatch.setenv("JOBPUNTER_DEPLOYMENT_REVISION", "d" * 40)
    get_settings.cache_clear()
    try:
        response = router_module.readiness(BrokenSession())
        rendered = response.body.decode("utf-8")
        assert response.status_code == 503
        assert "secret-host" not in rendered
        assert json.loads(rendered)["data"]["checks"]["database"] is False
    finally:
        get_settings.cache_clear()
