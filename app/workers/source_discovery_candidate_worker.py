"""Lease-aware worker for approved source-discovery candidates."""

from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.application.jobs.adaptive_dispatcher import AdaptiveAcquisitionDispatcher
from app.application.jobs.discovery_intake import (
    discovery_candidate_is_executable,
    invalidate_discovery_candidate_approval,
    mark_discovery_candidate_acquired,
)
from app.application.jobs.resolved_url_acquisition import (
    ROUTE_CONNECTOR_TYPES,
    ResolvedUrlAcquisitionService,
)
from app.application.jobs.url_source_resolver import RetrievalRoute
from app.application.operations.worker_heartbeat import (
    WorkerHeartbeatCallback,
    build_worker_heartbeat,
)
from app.infrastructure.acquisition.dispatch_ledger import SqlAlchemyDispatchAttemptLedger
from app.infrastructure.db.models.source import SourceConnector
from app.infrastructure.db.models.source_intelligence import (
    SourceDiscoveryCandidate,
    SourceIntelligenceProfile,
)
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.source_discovery_candidate_worker")


class SourceDiscoveryCandidateWorker:
    def __init__(
        self,
        session_factory=SessionLocal,
        *,
        lease_seconds: int = 300,
        max_attempts: int = 5,
    ) -> None:
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def claim_batch(self, limit: int = 10) -> list[UUID]:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(UTC)
        with self.session_factory() as db:
            rows = db.scalars(
                select(SourceDiscoveryCandidate)
                .where(
                    SourceDiscoveryCandidate.source_id.is_not(None),
                    or_(
                        SourceDiscoveryCandidate.status == "APPROVED",
                        and_(
                            SourceDiscoveryCandidate.status == "PROCESSING",
                            SourceDiscoveryCandidate.lease_expires_at <= now,
                        ),
                    ),
                    or_(
                        SourceDiscoveryCandidate.next_attempt_at.is_(None),
                        SourceDiscoveryCandidate.next_attempt_at <= now,
                    ),
                )
                .order_by(
                    SourceDiscoveryCandidate.next_attempt_at.asc().nullsfirst(),
                    SourceDiscoveryCandidate.first_discovered_at,
                    SourceDiscoveryCandidate.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            claimed: list[UUID] = []
            for row in rows:
                # A crashed process cannot call _release_for_retry(). Enforce
                # the same attempt ceiling when reclaiming an expired lease.
                if row.attempt_count >= self.max_attempts:
                    row.status = "FAILED"
                    row.lease_expires_at = None
                    row.next_attempt_at = None
                    row.last_error_category = "WORKER_LEASE_RETRIES_EXHAUSTED"
                    row.revision += 1
                    continue
                row.status = "PROCESSING"
                row.attempt_count += 1
                row.last_attempt_at = now
                row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                row.next_attempt_at = None
                row.last_error_category = None
                row.revision += 1
                claimed.append(row.id)
            db.commit()
            return claimed

    @staticmethod
    def _retry_delay(attempt_count: int) -> int:
        return min(30 * (2 ** max(attempt_count - 1, 0)), 3600)

    def _release_for_retry(
        self,
        candidate: SourceDiscoveryCandidate,
        profile: SourceIntelligenceProfile | None,
        error_category: str,
    ) -> str:
        if profile is None or not discovery_candidate_is_executable(profile, candidate):
            invalidate_discovery_candidate_approval(candidate)
            candidate.last_error_category = error_category[:64]
            candidate.revision += 1
            return "review_required"
        candidate.lease_expires_at = None
        candidate.last_error_category = error_category[:64]
        candidate.revision += 1
        if candidate.attempt_count >= self.max_attempts:
            candidate.status = "FAILED"
            candidate.next_attempt_at = None
            return "failed"
        candidate.status = "APPROVED"
        candidate.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=self._retry_delay(candidate.attempt_count)
        )
        return "retried"

    @staticmethod
    def _connector_ids(db, candidate: SourceDiscoveryCandidate) -> dict[RetrievalRoute, UUID]:
        routes = {
            RetrievalRoute(value)
            for value in candidate.recommended_routes
            if value != RetrievalRoute.REVIEW_REQUIRED.value
        }
        connector_types = {ROUTE_CONNECTOR_TYPES[route] for route in routes}
        connectors = db.scalars(
            select(SourceConnector)
            .where(
                SourceConnector.source_id == candidate.source_id,
                SourceConnector.status == "ACTIVE",
                SourceConnector.connector_type.in_(connector_types),
            )
            .order_by(SourceConnector.updated_at.desc(), SourceConnector.id)
        ).all()
        route_by_type = {
            connector_type: route for route, connector_type in ROUTE_CONNECTOR_TYPES.items()
        }
        selected: dict[RetrievalRoute, UUID] = {}
        for connector in connectors:
            route = route_by_type.get(connector.connector_type)
            if route in routes:
                selected.setdefault(route, connector.id)
        return selected

    def execute_candidate(self, candidate_id: UUID) -> str:
        with self.session_factory() as db:
            candidate = db.get(SourceDiscoveryCandidate, candidate_id)
            if candidate is None or candidate.status != "PROCESSING":
                return "skipped"
            profile = db.get(SourceIntelligenceProfile, candidate.profile_id)
            if not discovery_candidate_is_executable(profile, candidate):
                outcome = self._release_for_retry(
                    candidate, profile, "CANDIDATE_GOVERNANCE_REVIEW_REQUIRED"
                )
                db.commit()
                return outcome
            connector_ids = self._connector_ids(db, candidate)
            if not connector_ids:
                outcome = self._release_for_retry(
                    candidate, profile, "CANDIDATE_CONNECTORS_UNAVAILABLE"
                )
                db.commit()
                return outcome
            service = ResolvedUrlAcquisitionService(
                SqlAlchemyAcquisitionRepository(db),
                AdaptiveAcquisitionDispatcher(
                    SqlAlchemyDispatchAttemptLedger(db, profile_id=profile.id)
                ),
            )
            result = service.execute_candidate(
                source_id=candidate.source_id,
                profile=profile,
                candidate=candidate,
                operation_key=f"scheduled-candidate:{candidate.id}:attempt:{candidate.attempt_count}",
                connector_ids=connector_ids,
                max_attempts=min(len(connector_ids), 5),
            )
            db.refresh(candidate)
            db.refresh(profile)
            if candidate.status != "PROCESSING":
                return "skipped"
            if not discovery_candidate_is_executable(profile, candidate):
                outcome = self._release_for_retry(
                    candidate, profile, "CANDIDATE_GOVERNANCE_CHANGED"
                )
                db.commit()
                return outcome
            acquired_count = sum(item.acquired_count for item in result.attempts)
            if result.selected_route is not None and acquired_count > 0:
                mark_discovery_candidate_acquired(db, candidate)
                db.commit()
                return "acquired"
            outcome = self._release_for_retry(
                candidate, profile, result.error_category or "CANDIDATE_NO_RECORDS"
            )
            db.commit()
            return outcome

    def _record_failure(self, candidate_id: UUID, exc: Exception) -> str:
        with self.session_factory() as db:
            candidate = db.get(SourceDiscoveryCandidate, candidate_id)
            if candidate is None or candidate.status != "PROCESSING":
                return "skipped"
            profile = db.get(SourceIntelligenceProfile, candidate.profile_id)
            outcome = self._release_for_retry(candidate, profile, type(exc).__name__.upper())
            db.commit()
            return outcome

    def run_once(self, limit: int = 10) -> dict[str, int]:
        claimed = self.claim_batch(limit)
        counts = {
            "claimed": len(claimed),
            "acquired": 0,
            "retried": 0,
            "failed": 0,
            "review_required": 0,
            "skipped": 0,
        }
        for candidate_id in claimed:
            try:
                outcome = self.execute_candidate(candidate_id)
            except Exception as exc:  # noqa: BLE001 - isolate queue items and preserve retries
                logger.error(
                    "source discovery candidate failed candidate=%s error_type=%s",
                    candidate_id,
                    type(exc).__name__,
                )
                outcome = self._record_failure(candidate_id, exc)
            counts[outcome] += 1
        return counts


def run_worker(
    *,
    interval_seconds: float = 2.0,
    batch_size: int = 10,
    once: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    heartbeat: WorkerHeartbeatCallback | None = None,
    worker: SourceDiscoveryCandidateWorker | None = None,
) -> int:
    if interval_seconds <= 0 or batch_size < 1:
        raise ValueError("interval_seconds and batch_size must be positive")
    consumer = worker or SourceDiscoveryCandidateWorker()
    cycles = 0
    if heartbeat:
        heartbeat("RUNNING", {"batch_size": batch_size})
    try:
        while not should_stop():
            outcome = consumer.run_once(batch_size)
            cycles += 1
            if heartbeat:
                heartbeat(
                    "IDLE" if outcome["claimed"] == 0 else "RUNNING",
                    {**outcome, "cycles": cycles, "batch_size": batch_size},
                    successful_cycle=True,
                )
            if once:
                break
            if outcome["claimed"] == 0:
                sleep(interval_seconds)
    except Exception as exc:
        if heartbeat:
            heartbeat("FAILED", {"cycles": cycles}, type(exc).__name__)
        raise
    if heartbeat:
        heartbeat("STOPPED", {"cycles": cycles})
    return cycles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(
        interval_seconds=args.interval_seconds,
        batch_size=args.batch_size,
        once=args.once,
        should_stop=lambda: stopped,
        heartbeat=build_worker_heartbeat("source_discovery_candidate_worker"),
    )


if __name__ == "__main__":
    main()
