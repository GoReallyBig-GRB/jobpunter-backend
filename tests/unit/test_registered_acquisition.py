from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
from app.ports.acquisition import RawAcquisition


class Repository:
    def __init__(self, source_id, connector_id):
        self.source = SimpleNamespace(
            id=source_id, status="ACTIVE", configuration={"source_default": "source"}
        )
        self.connector = SimpleNamespace(
            source_id=source_id, connector_type="JOBSPY", connector_version="1.0.0",
            status="ACTIVE", configuration={"connector_default": "connector"},
        )
        self.runs = {}
        self.items = []

    def get_connector(self, connector_id):
        return self.connector if connector_id else None

    def get_source(self, source_id):
        return self.source if self.source and self.source.id == source_id else None

    def get_run(self, source_id, run_key):
        return self.runs.get((source_id, run_key))

    def create_run(self, source_id, connector_id, run_key):
        run = SimpleNamespace(id=uuid4(), status="RUNNING", metrics={})
        self.runs[(source_id, run_key)] = run
        return run, True

    def persist_raw_if_new(self, run_id, item):
        self.items.append(item)
        return True

    def complete_run(self, run_id, acquired_count, persisted_count):
        run = next(row for row in self.runs.values() if row.id == run_id)
        run.status = "SUCCEEDED"
        run.metrics = {"acquired_count": acquired_count, "persisted_count": persisted_count}

    def fail_run(self, run_id, *, error_summary, acquired_count=0, persisted_count=0):
        run = next(row for row in self.runs.values() if row.id == run_id)
        run.status = "FAILED"
        run.metrics = {"acquired_count": acquired_count, "persisted_count": persisted_count}
        run.error_summary = error_summary


class FakeConnector:
    connector_type = "JOBSPY"
    connector_version = "1.0.0"

    def acquire(self, *, source_id, context):
        self.last_context = context
        return [RawAcquisition(
            source_id=source_id,
            external_record_id="job-1",
            source_url="https://example.test/job-1",
            raw_content={"title": "Technical Product Manager"},
            content_hash="hash-1",
            observed_at=datetime.now(timezone.utc),
        )]


def test_registered_service_routes_persisted_connector_to_acquisition_service(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(
        service.registry,
        "create",
        lambda connector_type, connector_version: FakeConnector(),
    )

    result = service.run(source_id, connector_id, "mission-1", {"search_terms": ["TPM"]})

    assert result.status == "SUCCEEDED"
    assert result.acquired_count == 1
    assert result.persisted_count == 1
    assert len(repository.items) == 1


def test_registered_service_merges_saved_config_then_runtime_context(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.configuration = {"shared": "connector", "connector_only": True}
    repository.source.configuration = {"source_only": True, "shared": "source"}
    connector = FakeConnector()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: connector)

    service.run(
        source_id, connector_id, "mission-config",
        {"shared": "runtime", "runtime_only": True},
    )

    assert connector.last_context == {
        "source_only": True,
        "shared": "runtime",
        "connector_only": True,
        "runtime_only": True,
    }


import pytest


@pytest.mark.parametrize("status", ["PAUSED", "DISABLED", "UNKNOWN", ""])
def test_registered_service_rejects_inactive_source(status, monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.source.status = status
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: FakeConnector())

    with pytest.raises(ValueError, match="source is not active"):
        service.run(source_id, connector_id, "inactive-source", {})

    assert repository.runs == {}


@pytest.mark.parametrize("status", ["PAUSED", "DISABLED", "UNKNOWN", ""])
def test_registered_service_rejects_inactive_connector(status, monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.status = status
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: FakeConnector())

    with pytest.raises(ValueError, match="connector is not active"):
        service.run(source_id, connector_id, "inactive-connector", {})

    assert repository.runs == {}


def test_registered_service_rejects_non_object_runtime_context():
    source_id, connector_id = uuid4(), uuid4()
    service = RegisteredAcquisitionService(Repository(source_id, connector_id))

    with pytest.raises(ValueError, match="context must be an object"):
        service.run(source_id, connector_id, "bad-context", [])
