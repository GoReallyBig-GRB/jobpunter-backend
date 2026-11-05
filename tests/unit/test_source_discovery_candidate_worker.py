from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.jobs.url_source_resolver import RetrievalRoute
from app.workers.source_discovery_candidate_worker import (
    SourceDiscoveryCandidateWorker,
    run_worker,
)


def test_retry_backoff_is_bounded_and_terminal_attempt_fails():
    worker = SourceDiscoveryCandidateWorker(session_factory=lambda: None, max_attempts=5)
    candidate = SimpleNamespace(
        attempt_count=1,
        status="PROCESSING",
        lease_expires_at=object(),
        next_attempt_at=None,
        last_error_category=None,
        revision=1,
    )

    assert worker._release_for_retry(candidate, "UPSTREAM_UNAVAILABLE") == "retried"
    assert candidate.status == "APPROVED"
    assert candidate.next_attempt_at is not None
    assert candidate.lease_expires_at is None
    assert candidate.last_error_category == "UPSTREAM_UNAVAILABLE"

    candidate.attempt_count = 5
    candidate.status = "PROCESSING"
    assert worker._release_for_retry(candidate, "STILL_UNAVAILABLE") == "failed"
    assert candidate.status == "FAILED"
    assert candidate.next_attempt_at is None


def test_connector_selection_uses_active_routes_and_one_connector_per_route():
    source_id = uuid4()
    public_first = SimpleNamespace(id=uuid4(), source_id=source_id, connector_type="PUBLIC_HTTP")
    public_second = SimpleNamespace(id=uuid4(), source_id=source_id, connector_type="PUBLIC_HTTP")
    scrapy = SimpleNamespace(id=uuid4(), source_id=source_id, connector_type="SCRAPY")

    class Scalars:
        def all(self):
            return [public_first, public_second, scrapy]

    db = SimpleNamespace(scalars=lambda _statement: Scalars())
    candidate = SimpleNamespace(
        source_id=source_id,
        recommended_routes=["PUBLIC_HTTP", "SCRAPY", "REVIEW_REQUIRED"],
    )

    selected = SourceDiscoveryCandidateWorker._connector_ids(db, candidate)

    assert selected == {
        RetrievalRoute.PUBLIC_HTTP: public_first.id,
        RetrievalRoute.SCRAPY: scrapy.id,
    }


def test_run_once_isolates_candidate_failures_and_records_retry(monkeypatch):
    first, second = uuid4(), uuid4()
    worker = SourceDiscoveryCandidateWorker(session_factory=lambda: None)
    monkeypatch.setattr(worker, "claim_batch", lambda _limit: [first, second])

    def execute(candidate_id):
        if candidate_id == first:
            raise RuntimeError("provider failed")
        return "acquired"

    monkeypatch.setattr(worker, "execute_candidate", execute)
    monkeypatch.setattr(worker, "_record_failure", lambda _candidate_id, _exc: "retried")

    assert worker.run_once(2) == {
        "claimed": 2,
        "acquired": 1,
        "retried": 1,
        "failed": 0,
        "skipped": 0,
    }


def test_worker_loop_reports_heartbeat_and_stops_after_one_cycle():
    events = []
    worker = SimpleNamespace(
        run_once=lambda _limit: {
            "claimed": 0,
            "acquired": 0,
            "retried": 0,
            "failed": 0,
            "skipped": 0,
        }
    )

    cycles = run_worker(
        once=True,
        worker=worker,
        heartbeat=lambda *args, **kwargs: events.append((args, kwargs)),
    )

    assert cycles == 1
    assert [event[0][0] for event in events] == ["RUNNING", "IDLE", "STOPPED"]


@pytest.mark.parametrize("limit", [0, -1])
def test_claim_batch_rejects_invalid_limit(limit):
    worker = SourceDiscoveryCandidateWorker(session_factory=lambda: None)
    with pytest.raises(ValueError, match="positive"):
        worker.claim_batch(limit)
