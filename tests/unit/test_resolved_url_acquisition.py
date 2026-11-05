from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.jobs.adaptive_dispatcher import (
    AdaptiveAcquisitionDispatcher,
    InMemoryAttemptLedger,
)
from app.application.jobs.resolved_url_acquisition import ResolvedUrlAcquisitionService
from app.application.jobs.url_source_resolver import ResolutionSignals, RetrievalRoute


class Repository:
    def __init__(self):
        self.source_id = uuid4()
        self.source = SimpleNamespace(
            id=self.source_id, base_url="https://jobs.example.org/", status="ACTIVE"
        )
        self.connectors = {
            uuid4(): SimpleNamespace(
                source_id=self.source_id, connector_type="PUBLIC_HTTP", status="ACTIVE"
            ),
            uuid4(): SimpleNamespace(
                source_id=self.source_id, connector_type="SCRAPY", status="ACTIVE"
            ),
        }

    def get_source(self, source_id):
        return self.source if source_id == self.source_id else None

    def get_connector(self, connector_id):
        return self.connectors.get(connector_id)


def _profile(*routes):
    return SimpleNamespace(
        id=uuid4(),
        domain="jobs.example.org",
        status="APPROVED",
        approved_by_user_id=uuid4(),
        approved_at=datetime.now(UTC),
        capabilities={"routes": list(routes)},
        permission_evidence={
            "access_method": "PUBLIC_JOB_PAGE",
            "terms_reference": "https://jobs.example.org/terms",
            "rate_limit_policy": "one request per second",
            "rate_limit_reference": "https://jobs.example.org/robots.txt",
        },
    )


def test_resolved_url_executes_registered_least_cost_route_and_fallback(monkeypatch):
    repository = Repository()
    connector_ids = {
        RetrievalRoute.PUBLIC_HTTP: next(
            key
            for key, value in repository.connectors.items()
            if value.connector_type == "PUBLIC_HTTP"
        ),
        RetrievalRoute.SCRAPY: next(
            key for key, value in repository.connectors.items() if value.connector_type == "SCRAPY"
        ),
    }
    calls = []

    class Registered:
        def __init__(self, _repository):
            pass

        def run(self, source_id, connector_id, run_key, context):
            connector_type = repository.connectors[connector_id].connector_type
            calls.append((connector_type, run_key, context))
            return SimpleNamespace(
                status="UNAVAILABLE" if connector_type == "PUBLIC_HTTP" else "SUCCEEDED",
                acquired_count=0 if connector_type == "PUBLIC_HTTP" else 1,
                error_category=(
                    "UPSTREAM_UNAVAILABLE" if connector_type == "PUBLIC_HTTP" else None
                ),
            )

    monkeypatch.setattr(
        "app.application.jobs.resolved_url_acquisition.RegisteredAcquisitionService",
        Registered,
    )
    service = ResolvedUrlAcquisitionService(
        repository, AdaptiveAcquisitionDispatcher(InMemoryAttemptLedger())
    )
    result = service.execute(
        source_id=repository.source_id,
        profile=_profile("PUBLIC_HTTP", "SCRAPY"),
        operation_key="resolved-1",
        url="https://jobs.example.org/job-1",
        connector_ids=connector_ids,
        signals=ResolutionSignals(body_sample='{"@type":"JobPosting"}'),
    )

    assert result.selected_route == RetrievalRoute.SCRAPY
    assert [item[0] for item in calls] == ["PUBLIC_HTTP", "SCRAPY"]
    assert calls[0][2] == {"page_url": "https://jobs.example.org/job-1"}
    assert calls[1][2] == {"start_urls": ["https://jobs.example.org/job-1"]}


def test_resolved_url_rejects_domain_not_bound_to_approved_source():
    repository = Repository()
    service = ResolvedUrlAcquisitionService(
        repository, AdaptiveAcquisitionDispatcher(InMemoryAttemptLedger())
    )
    with pytest.raises(PermissionError, match="not bound"):
        service.execute(
            source_id=repository.source_id,
            profile=_profile("PUBLIC_HTTP"),
            operation_key="resolved-2",
            url="https://other.example.org/job-1",
            connector_ids={},
            signals=ResolutionSignals(body_sample='{"@type":"JobPosting"}'),
        )


def test_resolved_url_records_restricted_attempt_for_unapproved_route():
    repository = Repository()
    public_connector = next(
        key for key, value in repository.connectors.items() if value.connector_type == "PUBLIC_HTTP"
    )
    service = ResolvedUrlAcquisitionService(
        repository, AdaptiveAcquisitionDispatcher(InMemoryAttemptLedger())
    )
    result = service.execute(
        source_id=repository.source_id,
        profile=_profile("PLAYWRIGHT"),
        operation_key="resolved-3",
        url="https://jobs.example.org/job-1",
        connector_ids={RetrievalRoute.PUBLIC_HTTP: public_connector},
        signals=ResolutionSignals(body_sample='{"@type":"JobPosting"}'),
        max_attempts=1,
    )
    assert result.status.value == "RESTRICTED"
    assert result.error_category == "ROUTE_NOT_PERMITTED"


def test_approved_candidate_executes_its_persisted_recommendation(monkeypatch):
    repository = Repository()
    public_connector = next(
        key for key, value in repository.connectors.items() if value.connector_type == "PUBLIC_HTTP"
    )
    profile = _profile("PUBLIC_HTTP")
    candidate = SimpleNamespace(
        profile_id=profile.id,
        source_id=repository.source_id,
        status="APPROVED",
        approved_by_user_id=uuid4(),
        approved_at=datetime.now(UTC),
        normalized_url="https://jobs.example.org/job-1",
        source_kind="JSON_LD",
        recommended_routes=["PUBLIC_HTTP"],
        confidence=0.94,
        evidence=[{"code": "JSON_LD_TYPE", "value": "JobPosting"}],
    )

    class Registered:
        def __init__(self, _repository):
            pass

        def run(self, source_id, connector_id, run_key, context):
            return SimpleNamespace(status="SUCCEEDED", acquired_count=1, error_category=None)

    monkeypatch.setattr(
        "app.application.jobs.resolved_url_acquisition.RegisteredAcquisitionService",
        Registered,
    )
    service = ResolvedUrlAcquisitionService(
        repository, AdaptiveAcquisitionDispatcher(InMemoryAttemptLedger())
    )

    result = service.execute_candidate(
        source_id=repository.source_id,
        profile=profile,
        candidate=candidate,
        operation_key="candidate-1",
        connector_ids={RetrievalRoute.PUBLIC_HTTP: public_connector},
    )

    assert result.selected_route == RetrievalRoute.PUBLIC_HTTP
    assert result.attempts[0].acquired_count == 1


def test_unapproved_candidate_cannot_execute():
    repository = Repository()
    profile = _profile("PUBLIC_HTTP")
    candidate = SimpleNamespace(
        profile_id=profile.id,
        status="REVIEW_REQUIRED",
        normalized_url="https://jobs.example.org/job-1",
    )
    service = ResolvedUrlAcquisitionService(
        repository, AdaptiveAcquisitionDispatcher(InMemoryAttemptLedger())
    )

    with pytest.raises(PermissionError, match="not approved"):
        service.execute_candidate(
            source_id=repository.source_id,
            profile=profile,
            candidate=candidate,
            operation_key="candidate-2",
            connector_ids={},
        )
