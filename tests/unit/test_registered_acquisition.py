from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from uuid import uuid4

from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
from app.infrastructure.acquisition.json_api import JsonApiConnector
from app.ports.acquisition import RawAcquisition


class Repository:
    def __init__(self, source_id, connector_id):
        self.source = SimpleNamespace(
            id=source_id, status="ACTIVE", configuration={"source_default": "source"}, permission_review_status="APPROVED", permission_review_reference="review://approved", permission_reviewed_at=datetime.now(timezone.utc), operational_state="UNAVAILABLE", last_error_category=None
        )
        self.connector = SimpleNamespace(
            source_id=source_id, connector_type="JOBSPY", connector_version="1.0.0",
            status="ACTIVE", configuration={"connector_default": "connector"},
        )
        self.runs = {}
        self.items = []
        self.rules = []

    def get_connector(self, connector_id):
        return self.connector if connector_id else None

    def get_source(self, source_id):
        return self.source if self.source and self.source.id == source_id else None

    def list_active_forbidden_target_rules(self):
        return list(self.rules)

    def record_source_operational_state(self, source_id, state, error_category=None):
        self.source.operational_state = state
        self.source.last_error_category = error_category

    def get_run(self, source_id, run_key):
        return self.runs.get((source_id, run_key))

    def create_run(self, source_id, connector_id, run_key):
        run = SimpleNamespace(id=uuid4(), status="RUNNING", metrics={})
        self.runs[(source_id, run_key)] = run
        return run, True

    def persist_raw_if_new(self, run_id, item):
        self.items.append(item)
        return True

    def complete_run(
        self, run_id, acquired_count, persisted_count, *, status="SUCCEEDED",
        error_category=None, failure_count=0, failure_categories=None,
        retry_after_seconds=None,
    ):
        run = next(row for row in self.runs.values() if row.id == run_id)
        run.status = status
        run.metrics = {
            "acquired_count": acquired_count,
            "persisted_count": persisted_count,
            "failure_count": failure_count,
            "failure_categories": failure_categories or [],
            "retry_after_seconds": retry_after_seconds,
        }
        run.error_category = error_category

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

    expected_context = {
        "source_only": True,
        "shared": "runtime",
        "connector_only": True,
        "runtime_only": True,
        "forbidden_target_rules": [],
    }
    assert {key: value for key, value in connector.last_context.items()
            if key != "acquisition_policy_revalidator"} == expected_context
    assert callable(connector.last_context["acquisition_policy_revalidator"])


@pytest.mark.parametrize("permission_status", [None, "", "PENDING", "REJECTED", "NOT_REQUIRED"])
def test_registered_service_fails_closed_without_approved_permission_review(permission_status, monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.source.permission_review_status = permission_status
    repository.source.permission_review_reference = None
    repository.source.permission_reviewed_at = None
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: FakeConnector())

    with pytest.raises(ValueError, match="permission review is not approved"):
        service.run(source_id, connector_id, "unapproved-source", {})

    assert repository.runs == {}
    assert repository.source.operational_state == "RESTRICTED"
    assert repository.source.last_error_category == "PERMISSION_REVIEW_REQUIRED"


def test_registered_service_allows_explicitly_approved_permission_review(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.source.permission_review_status = " approved "
    repository.source.permission_review_reference = "terms-review://123"
    repository.source.permission_reviewed_at = datetime.now(timezone.utc)
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: FakeConnector())

    result = service.run(source_id, connector_id, "approved-source", {})

    assert result.status == "SUCCEEDED"


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

def test_registered_service_records_restricted_run_when_jobspy_cannot_enforce_active_rules(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.rules = [{
        "id": str(uuid4()),
        "rule_type": "HOST_SUFFIX",
        "pattern": "example.com",
        "connector_scope": "ALL",
        "reason": "Test deny rule",
        "active": True,
    }]
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: __import__(
        "app.infrastructure.acquisition.jobspy", fromlist=["JobSpyConnector"]
    ).JobSpyConnector())

    result = service.run(source_id, connector_id, "blocked-jobspy-run", {
        "search_terms": ["product manager"],
        "location": "Remote",
    })

    assert result.status == "RESTRICTED"
    assert repository.runs[(source_id, "blocked-jobspy-run")].status == "RESTRICTED"
    assert repository.items == []

def test_registered_service_enforces_persisted_rule_and_records_restricted_json_run(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"
    repository.connector.configuration = {"endpoint_url": "https://blocked.example/jobs"}
    repository.rules = [{
        "id": str(uuid4()),
        "rule_type": "HOST_SUFFIX",
        "pattern": "example",
        "connector_scope": "ALL",
        "reason": "Test target restriction",
        "active": True,
    }]

    class NoNetworkClient:
        calls = 0

        def stream(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("target policy must reject before network")

    client = NoNetworkClient()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(
        service.registry,
        "create",
        lambda *_: JsonApiConnector(http_client=client, allow_unsafe_http_client=True),
    )

    result = service.run(source_id, connector_id, "blocked-json-run", {})

    assert result.status == "RESTRICTED"
    assert repository.runs[(source_id, "blocked-json-run")].status == "RESTRICTED"
    assert repository.items == []
    assert client.calls == 0



def test_registered_service_revalidates_live_policy_before_each_network_step(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"

    class TwoStepConnector:
        connector_type = "JSON_API"
        connector_version = "1.0.0"
        def __init__(self):
            self.network_calls = 0
        def acquire(self, *, source_id, context):
            revalidate = context["acquisition_policy_revalidator"]
            revalidate(url="https://public.example.org/page/1", connector_type=self.connector_type, method="GET")
            self.network_calls += 1
            repository.rules = [{
                "id": "new-rule", "rule_type": "HOST_EXACT",
                "pattern": "blocked.example.org", "connector_scope": "JSON_API",
                "reason": "Newly forbidden during acquisition", "active": True,
            }]
            revalidate(url="https://blocked.example.org/page/2", connector_type=self.connector_type, method="GET")
            self.network_calls += 1
            raise AssertionError("the blocked second request must never execute")

    connector = TwoStepConnector()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: connector)
    result = service.run(source_id, connector_id, "live-policy-refresh", {})
    assert result.status == "RESTRICTED"
    assert repository.runs[(source_id, "live-policy-refresh")].error_category == "FORBIDDEN_TARGET_RULE_MATCHED"
    assert connector.network_calls == 1
    assert repository.runs[(source_id, "live-policy-refresh")].status == "RESTRICTED"


def test_registered_service_revalidates_permission_revocation_before_next_step(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"

    class RevocationConnector:
        connector_type = "JSON_API"
        connector_version = "1.0.0"
        def __init__(self):
            self.network_calls = 0
        def acquire(self, *, source_id, context):
            revalidate = context["acquisition_policy_revalidator"]
            revalidate(url="https://public.example.org/page/1", connector_type=self.connector_type, method="GET")
            self.network_calls += 1
            repository.source.permission_review_status = "REVOKED"
            revalidate(url="https://public.example.org/page/2", connector_type=self.connector_type, method="GET")
            self.network_calls += 1
            raise AssertionError("revoked permission must stop the next request")

    connector = RevocationConnector()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: connector)
    result = service.run(source_id, connector_id, "permission-revoked-mid-run", {})
    assert result.status == "RESTRICTED"
    assert repository.runs[(source_id, "permission-revoked-mid-run")].error_category == "PERMISSION_REVIEW_REQUIRED"
    assert connector.network_calls == 1
    assert repository.runs[(source_id, "permission-revoked-mid-run")].status == "RESTRICTED"
