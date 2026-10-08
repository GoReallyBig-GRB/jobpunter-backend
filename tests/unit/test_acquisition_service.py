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
        existing = self.get_run(source_id, run_key)
        if existing:
            return existing, False
        run = SimpleNamespace(
            id=uuid4(), status="RUNNING", metrics={}, error_summary=None
        )
        self.runs[(source_id, run_key)] = run
        return run, True

    def persist_raw_if_new(self, run_id, item):
        deduplication_key = (
            f"id:{item.external_record_id}"
            if item.external_record_id is not None
            else f"hash:{item.content_hash}"
        )
        key = (item.source_id, deduplication_key, item.content_hash)
        if any(existing == key for existing in self.items):
            return False
        self.items.append(key)
        return True

    def complete_run(self, run_id, acquired_count, persisted_count):
        run = next(run for run in self.runs.values() if run.id == run_id)
        run.status = "SUCCEEDED"
        run.metrics = {
            "acquired_count": acquired_count,
            "persisted_count": persisted_count,
        }

    def fail_run(
        self, run_id, *, error_summary, acquired_count=0, persisted_count=0
    ):
        run = next(run for run in self.runs.values() if run.id == run_id)
        run.status = "FAILED"
        run.error_summary = error_summary
        run.metrics = {
            "acquired_count": acquired_count,
            "persisted_count": persisted_count,
        }


class Connector:
    connector_type = "TEST"
    connector_version = "1"

    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.calls = 0

    def acquire(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.items


def make_item(source, external_id, content_hash, title="PM"):
    return RawAcquisition(
        source,
        external_id,
        f"https://example.test/{external_id}" if external_id else None,
        {"title": title},
        content_hash,
        datetime.now(timezone.utc),
    )


def test_acquisition_is_idempotent_by_run_key():
    source = uuid4()
    connector_id = uuid4()
    item = make_item(source, "ext-1", "hash-1")
    repo = Repo()
    repo.connector.source_id = source
    connector = Connector([item])
    service = AcquisitionService(repo, connector)

    first = service.run(source, connector_id, "run-1", {})
    second = service.run(source, connector_id, "run-1", {})

    assert first.run_id == second.run_id
    assert first.status == second.status == "SUCCEEDED"
    assert first.persisted_count == 1
    assert second.acquired_count == 1
    assert second.persisted_count == 1
    assert connector.calls == 1


def test_same_source_record_and_content_are_not_persisted_twice():
    source = uuid4()
    connector_id = uuid4()
    item = make_item(source, "ext-1", "hash-1")
    repo = Repo()
    repo.connector.source_id = source

    result = AcquisitionService(repo, Connector([item, item])).run(
        source, connector_id, "run-2", {}
    )

    assert result.acquired_count == 2
    assert result.persisted_count == 1


def test_distinct_external_records_with_same_content_are_preserved():
    source = uuid4()
    connector_id = uuid4()
    items = [
        make_item(source, "ext-1", "same-hash"),
        make_item(source, "ext-2", "same-hash"),
    ]
    repo = Repo()
    repo.connector.source_id = source

    result = AcquisitionService(repo, Connector(items)).run(
        source, connector_id, "run-3", {}
    )

    assert result.persisted_count == 2


def test_changed_content_for_same_external_record_is_preserved():
    source = uuid4()
    connector_id = uuid4()
    first_item = make_item(source, "ext-1", "hash-v1", title="Product Manager")
    changed_item = make_item(source, "ext-1", "hash-v2", title="Senior Product Manager")
    repo = Repo()
    repo.connector.source_id = source

    first = AcquisitionService(repo, Connector([first_item])).run(
        source, connector_id, "run-4", {}
    )
    second = AcquisitionService(repo, Connector([changed_item])).run(
        source, connector_id, "run-5", {}
    )

    assert first.persisted_count == 1
    assert second.persisted_count == 1
    assert len(repo.items) == 2


def test_connector_failure_is_recorded_and_run_is_not_retried_with_same_key():
    source = uuid4()
    connector_id = uuid4()
    repo = Repo()
    repo.connector.source_id = source
    connector = Connector(error=RuntimeError("sensitive upstream detail"))
    service = AcquisitionService(repo, connector)

    with pytest.raises(RuntimeError):
        service.run(source, connector_id, "run-fail", {})

    failed = repo.get_run(source, "run-fail")
    assert failed.status == "FAILED"
    assert failed.error_summary == "connector_error:RuntimeError"
    assert "sensitive upstream detail" not in failed.error_summary

    replay = service.run(source, connector_id, "run-fail", {})
    assert replay.status == "FAILED"
    assert connector.calls == 1
