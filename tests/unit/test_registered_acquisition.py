from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import socket
from types import SimpleNamespace

import httpx

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
            if key not in {
                "acquisition_policy_revalidator",
                "persisted_connector_configuration",
                "persisted_connector_capabilities",
            }} == expected_context
    assert connector.last_context["persisted_connector_configuration"] == repository.connector.configuration
    assert connector.last_context["persisted_connector_capabilities"] == {}
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


def test_registered_service_stops_when_connector_configuration_changes_mid_run(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"
    repository.connector.configuration = {"endpoint_url": "https://public.example.org/page"}
    
    class ConfigChangeConnector:
        connector_type = "JSON_API"
        connector_version = "1.0.0"
        def __init__(self):
            self.network_calls = 0
        def acquire(self, *, source_id, context):
            revalidate = context["acquisition_policy_revalidator"]
            revalidate(url="https://public.example.org/page/1", connector_type=self.connector_type, method="GET")
            self.network_calls += 1
            repository.connector.configuration = {"endpoint_url": "https://other.example.org/page"}
            revalidate(url="https://public.example.org/page/2", connector_type=self.connector_type, method="GET")
            self.network_calls += 1
            raise AssertionError("configuration change must stop the next request")

    connector = ConfigChangeConnector()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: connector)
    result = service.run(source_id, connector_id, "config-changed-mid-run", {})

    assert result.status == "RESTRICTED"
    assert repository.runs[(source_id, "config-changed-mid-run")].error_category == "ACQUISITION_CONFIGURATION_CHANGED"
    assert connector.network_calls == 1


@pytest.mark.parametrize(
    "change,method,expected_category",
    [
        ("source_configuration", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_base_url", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_type", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("permission_review_reference", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("permission_reviewed_at", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("active_configuration_version", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_configuration", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_capabilities", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_limits", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_type", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_version", "GET", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_deactivated", "GET", "SOURCE_POLICY_CHANGED"),
        ("connector_deactivated", "GET", "CONNECTOR_POLICY_CHANGED"),
        ("unsupported_method_post", "POST", "ACQUISITION_METHOD_NOT_ALLOWED"),
        ("unsupported_method_put", "PUT", "ACQUISITION_METHOD_NOT_ALLOWED"),
        ("unsupported_method_patch", "PATCH", "ACQUISITION_METHOD_NOT_ALLOWED"),
        ("unsupported_method_delete", "DELETE", "ACQUISITION_METHOD_NOT_ALLOWED"),
        ("unsupported_method_head", "HEAD", "ACQUISITION_METHOD_NOT_ALLOWED"),
        ("unsupported_method_options", "OPTIONS", "ACQUISITION_METHOD_NOT_ALLOWED"),
        ("unsupported_method_trace", "TRACE", "ACQUISITION_METHOD_NOT_ALLOWED"),
    ],
)
def test_registered_service_revalidates_remaining_policy_changes(
    monkeypatch, change, method, expected_category
):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"

    class PolicyChangeConnector:
        connector_type = "JSON_API"
        connector_version = "1.0.0"
        network_calls = 0

        def acquire(self, *, source_id, context):
            guard = context["acquisition_policy_revalidator"]
            guard(
                url="https://public.example.org/page/1",
                connector_type=self.connector_type,
                method="GET",
            )
            self.network_calls += 1
            if change == "source_configuration":
                repository.source.configuration = {"source_default": "changed"}
            elif change == "source_base_url":
                repository.source.base_url = "https://changed.example.org"
            elif change == "source_type":
                repository.source.source_type = "CHANGED"
            elif change == "permission_review_reference":
                repository.source.permission_review_reference = "review://updated"
            elif change == "permission_reviewed_at":
                repository.source.permission_reviewed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
            elif change == "active_configuration_version":
                repository.connector.active_configuration_version = 2
            elif change == "connector_configuration":
                repository.connector.configuration = {"endpoint_url": "https://changed.example.org"}
            elif change == "connector_capabilities":
                repository.connector.capabilities = {"supports_pagination": False}
            elif change == "connector_limits":
                repository.connector.limits = {"max_pages": 1}
            elif change == "connector_type":
                repository.connector.connector_type = "RSS_ATOM"
            elif change == "connector_version":
                repository.connector.connector_version = "2.0.0"
            elif change == "source_deactivated":
                repository.source.status = "INACTIVE"
            elif change == "connector_deactivated":
                repository.connector.status = "INACTIVE"
            guard(
                url="https://public.example.org/page/2",
                connector_type=self.connector_type,
                method=method,
            )
            self.network_calls += 1
            raise AssertionError("policy violation must stop the next request")

    connector = PolicyChangeConnector()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: connector)
    result = service.run(source_id, connector_id, f"policy-change-{change}", {})

    assert result.status == "RESTRICTED"
    run = repository.runs[(source_id, f"policy-change-{change}")]
    assert run.status == "RESTRICTED"
    assert run.error_category == expected_category
    assert connector.network_calls == 1


@pytest.mark.parametrize(("initial_version", "updated_version"), [(1, 2), (2, 1)])
def test_registered_json_api_stops_after_active_configuration_version_changes_mid_run(
    monkeypatch, initial_version, updated_version
):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"
    repository.connector.configuration = {
        "endpoint_url": "https://api.example.com/jobs",
        "page_param": "page",
        "max_pages": 2,
    }
    repository.connector.active_configuration_version = initial_version

    def public_dns(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", public_dns)

    class ChangingVersionClient:
        def __init__(self):
            self.calls = []

        @contextmanager
        def stream(self, method, url, *, params=None, headers=None, timeout=None, follow_redirects=None):
            self.calls.append((method, url, params))
            request = httpx.Request(method, url, params=params)
            response = httpx.Response(
                200,
                json={"jobs": [{
                    "id": "job-1",
                    "title": "Technical Product Manager",
                    "company": "Example",
                    "location": "Remote",
                    "description": "Lead product delivery",
                    "url": "https://jobs.example.com/1",
                }]},
                request=request,
            )
            yield response
            if len(self.calls) == 1:
                repository.connector.active_configuration_version = updated_version

    client = ChangingVersionClient()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(
        service.registry,
        "create",
        lambda *_: JsonApiConnector(http_client=client, allow_unsafe_http_client=True),
    )

    run_key = f"config-version-{initial_version}-to-{updated_version}"
    result = service.run(source_id, connector_id, run_key, {})

    assert result.status == "PARTIAL"
    run = repository.runs[(source_id, run_key)]
    assert run.status == "PARTIAL"
    assert run.error_category == "ACQUISITION_CONFIGURATION_CHANGED"
    assert len(client.calls) == 1
    assert len(repository.items) == 1



@pytest.mark.parametrize(
    ("method", "configuration", "capabilities", "expected_error"),
    [
        ("GET", {}, {}, None),
        ("POST", {}, {}, "ACQUISITION_METHOD_NOT_ALLOWED"),
        (
            "POST",
            {"request_method": "POST", "read_only_post_action": "search_jobs"},
            {"read_only_post": True},
            None,
        ),
        (
            "POST",
            {"request_method": "POST", "read_only_post_action": "search_jobs"},
            {"read_only_post": False},
            "ACQUISITION_METHOD_NOT_ALLOWED",
        ),
        (
            "POST",
            {"request_method": "GET", "read_only_post_action": "search_jobs"},
            {"read_only_post": True},
            "ACQUISITION_METHOD_NOT_ALLOWED",
        ),
        (
            "PUT",
            {"request_method": "POST", "read_only_post_action": "search_jobs"},
            {"read_only_post": True},
            "ACQUISITION_METHOD_NOT_ALLOWED",
        ),
    ],
)
def test_registered_acquisition_method_guard_requires_explicit_persisted_post_approval(
    method, configuration, capabilities, expected_error
):
    from app.application.jobs.registered_acquisition import (
        build_acquisition_policy_revalidator,
    )
    from app.ports.acquisition import AcquisitionAdapterError

    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"
    repository.connector.configuration = configuration
    repository.connector.capabilities = capabilities
    repository.connector.limits = {}
    repository.connector.active_configuration_version = 1
    repository.connector.connector_version = "1.0.0"
    guard = build_acquisition_policy_revalidator(
        repository, source_id, connector_id, repository.source, repository.connector
    )

    if expected_error:
        with pytest.raises(AcquisitionAdapterError) as exc_info:
            guard(
                url="https://api.example.com/jobs",
                connector_type="JSON_API",
                method=method,
            )
        assert exc_info.value.error_category == expected_error
    else:
        guard(
            url="https://api.example.com/jobs",
            connector_type="JSON_API",
            method=method,
        )


def test_registered_acquisition_post_approval_is_revoked_before_next_request():
    from app.application.jobs.registered_acquisition import (
        build_acquisition_policy_revalidator,
    )
    from app.ports.acquisition import AcquisitionAdapterError

    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"
    repository.connector.configuration = {
        "request_method": "POST",
        "read_only_post_action": "search_jobs",
    }
    repository.connector.capabilities = {"read_only_post": True}
    repository.connector.limits = {}
    repository.connector.active_configuration_version = 1
    repository.connector.connector_version = "1.0.0"
    guard = build_acquisition_policy_revalidator(
        repository, source_id, connector_id, repository.source, repository.connector
    )

    guard(
        url="https://api.example.com/jobs",
        connector_type="JSON_API",
        method="POST",
    )
    repository.connector.capabilities = {"read_only_post": False}

    with pytest.raises(AcquisitionAdapterError) as exc_info:
        guard(
            url="https://api.example.com/jobs?page=2",
            connector_type="JSON_API",
            method="POST",
        )

    assert exc_info.value.error_category == "ACQUISITION_METHOD_NOT_ALLOWED"


def test_registered_service_prevents_runtime_context_from_granting_post(monkeypatch):
    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"
    repository.connector.configuration = {
        "endpoint_url": "https://api.example.com/jobs",
        "request_method": "GET",
    }
    repository.connector.capabilities = {"read_only_post": False}
    connector = FakeConnector()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(service.registry, "create", lambda *_: connector)

    service.run(
        source_id,
        connector_id,
        "runtime-cannot-grant-post",
        {
            "request_method": "POST",
            "read_only_post_action": "search_jobs",
            "request_body": {"query": "jobs"},
            "persisted_connector_configuration": {
                "request_method": "POST",
                "read_only_post_action": "runtime-forged",
                "request_body": {"forged": True},
            },
            "persisted_connector_capabilities": {"read_only_post": True},
        },
    )

    assert connector.last_context["persisted_connector_configuration"] == {
        "endpoint_url": "https://api.example.com/jobs",
        "request_method": "GET",
    }
    assert connector.last_context["persisted_connector_capabilities"] == {
        "read_only_post": False,
    }

def test_registered_service_executes_only_persisted_approved_json_api_post(monkeypatch):
    from contextlib import contextmanager
    from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport

    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "JSON_API"
    repository.connector.configuration = {
        "endpoint_url": "https://api.example.com/search",
        "items_path": "jobs",
        "request_method": "POST",
        "read_only_post_action": "search_jobs",
        "request_body": {"query": "product manager", "limit": 10},
    }
    repository.connector.capabilities = {"read_only_post": True}
    repository.connector.limits = {}
    repository.connector.active_configuration_version = 1

    def public_dns(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", public_dns)

    class FakePinnedTransport(PublicAddressHTTPTransport):
        def __init__(self):
            pass

    class PinnedTestClient:
        def __init__(self):
            self._transport = FakePinnedTransport()
            self._mounts = {}
            self.calls = []

        @contextmanager
        def stream(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            request = httpx.Request(method, url)
            response = httpx.Response(
                200,
                json={"jobs": [{
                    "id": "job-1",
                    "title": "Technical Product Manager",
                    "company": "Example",
                    "location": "Remote",
                    "description": "Lead product delivery",
                    "url": "https://jobs.example.com/1",
                }]},
                headers={"content-type": "application/json"},
                request=request,
            )
            yield response

    client = PinnedTestClient()
    service = RegisteredAcquisitionService(repository)
    monkeypatch.setattr(
        service.registry,
        "create",
        lambda *_: JsonApiConnector(http_client=client, allow_unsafe_http_client=True),
    )

    result = service.run(
        source_id,
        connector_id,
        "approved-read-only-post",
        {
            "request_method": "GET",
            "read_only_post_action": "runtime-forged",
            "request_body": {"forged": True},
            "persisted_connector_configuration": {"request_method": "POST"},
            "persisted_connector_capabilities": {"read_only_post": True},
        },
    )

    assert result.status == "SUCCEEDED"
    assert result.acquired_count == 1
    assert len(client.calls) == 1
    method, url, options = client.calls[0]
    assert method == "POST"
    assert url == "https://api.example.com/search"
    assert json.loads(options["content"]) == {"query": "product manager", "limit": 10}
    assert options["follow_redirects"] is False

def test_registered_guard_rejects_post_for_connectors_without_reviewed_json_api_transport():
    from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
    from app.ports.acquisition import AcquisitionAdapterError

    source_id, connector_id = uuid4(), uuid4()
    repository = Repository(source_id, connector_id)
    repository.connector.connector_type = "GREENHOUSE_JOB_BOARD"
    repository.connector.configuration = {
        "request_method": "POST",
        "read_only_post_action": "search_jobs",
    }
    repository.connector.capabilities = {"read_only_post": True}
    repository.connector.limits = {}
    repository.connector.active_configuration_version = 1
    guard = build_acquisition_policy_revalidator(
        repository, source_id, connector_id, repository.source, repository.connector
    )

    with pytest.raises(AcquisitionAdapterError) as exc_info:
        guard(
            url="https://boards-api.greenhouse.io/test",
            connector_type="GREENHOUSE_JOB_BOARD",
            method="POST",
        )

    assert exc_info.value.error_category == "ACQUISITION_METHOD_NOT_ALLOWED"

