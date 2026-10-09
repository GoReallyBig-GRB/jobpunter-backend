from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.jobs.service import AcquisitionService
from app.ports.acquisition import RawAcquisition


class Repo:
    def __init__(self):
        self.runs = {}
        self.items = []
        self.connector = SimpleNamespace(source_id=None)

    def get_run(self, source_id, run_key):
        return self.runs.get((source_id, run_key))

    def get_connector(self, connector_id):
        return self.connector

    def create_run(self, source_id, connector_id, run_key):
        run = SimpleNamespace(id=uuid4(), metrics={}, status="RUNNING", error_summary=None)
        self.runs[(source_id, run_key)] = run
        return run, True

    def persist_raw_if_new(self, run_id, item):
        if item.external_record_id:
            identity = (item.source_id, "external", item.external_record_id, item.content_hash)
        elif item.source_url:
            identity = (item.source_id, "url", item.source_url, item.content_hash)
        else:
            identity = (item.source_id, "hash", item.content_hash)
        if identity in self.items:
            return False
        self.items.append(identity)
        return True

    def complete_run(self, run_id, acquired_count, persisted_count):
        run = next(run for run in self.runs.values() if run.id == run_id)
        run.status = "SUCCEEDED"
        run.metrics = {"acquired_count": acquired_count, "persisted_count": persisted_count}

    def fail_run(self, run_id, *, error_summary, acquired_count=0, persisted_count=0):
        run = next(run for run in self.runs.values() if run.id == run_id)
        run.status = "FAILED"
        run.metrics = {"acquired_count": acquired_count, "persisted_count": persisted_count}
        run.error_summary = error_summary


class Connector:
    connector_type = "TEST"
    connector_version = "1"

    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error

    def acquire(self, **kwargs):
        if self.error:
            raise self.error
        return self.items


def make_item(source, external_id="ext-1", url="https://example.test/1", raw=None, hash_value="hash-1"):
    return RawAcquisition(
        source,
        external_id,
        url,
        raw or {"title": "PM"},
        hash_value,
        datetime.now(timezone.utc),
    )


def test_acquisition_is_idempotent_by_run_key():
    source = uuid4()
    connector_id = uuid4()
    item = make_item(source)
    repo = Repo()
    repo.connector.source_id = source
    service = AcquisitionService(repo, Connector([item]))

    first = service.run(source, connector_id, "run-1", {})
    second = service.run(source, connector_id, "run-1", {})

    assert first.run_id == second.run_id
    assert first.persisted_count == 1
    assert first.status == "SUCCEEDED"
    assert second.acquired_count == 1
    assert second.persisted_count == 1


def test_identical_source_snapshot_is_not_persisted_twice():
    source = uuid4()
    connector_id = uuid4()
    item = make_item(source)
    repo = Repo()
    repo.connector.source_id = source

    result = AcquisitionService(repo, Connector([item, item])).run(source, connector_id, "run-2", {})

    assert result.acquired_count == 2
    assert result.persisted_count == 1


def test_changed_content_for_same_external_id_is_preserved_as_new_snapshot():
    source = uuid4()
    connector_id = uuid4()
    first = make_item(source, raw={"title": "PM"}, hash_value="hash-1")
    second = make_item(source, raw={"title": "Senior PM"}, hash_value="hash-2")
    repo = Repo()
    repo.connector.source_id = source

    result = AcquisitionService(repo, Connector([first, second])).run(source, connector_id, "run-3", {})

    assert result.acquired_count == 2
    assert result.persisted_count == 2


def test_connector_failure_marks_run_failed_and_reraises():
    source = uuid4()
    connector_id = uuid4()
    repo = Repo()
    repo.connector.source_id = source
    error = RuntimeError("private token should not be persisted")
    service = AcquisitionService(repo, Connector(error=error))

    with pytest.raises(RuntimeError, match="private token"):
        service.run(source, connector_id, "run-4", {})

    run = repo.runs[(source, "run-4")]
    assert run.status == "FAILED"
    assert run.error_summary == "connector_error:RuntimeError"
