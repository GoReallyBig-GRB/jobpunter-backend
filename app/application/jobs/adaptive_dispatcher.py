"""Bounded least-cost acquisition routing with governance on every attempt."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.application.jobs.url_source_resolver import RetrievalRecommendation, RetrievalRoute
from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionOutcome,
)

ROUTE_COST = {
    RetrievalRoute.JSON_API: 10,
    RetrievalRoute.RSS_ATOM: 20,
    RetrievalRoute.PUBLIC_HTTP: 30,
    RetrievalRoute.SCRAPY: 40,
    RetrievalRoute.PLAYWRIGHT: 50,
    RetrievalRoute.REVIEW_REQUIRED: 10_000,
}


@dataclass(frozen=True)
class RouteHealth:
    circuit_open: bool = False
    healthy: bool = True


@dataclass(frozen=True)
class DispatchAttempt:
    attempt_key: str
    url_sha256: str
    route: RetrievalRoute
    status: AcquisitionOutcome
    error_category: str | None
    acquired_count: int


@dataclass(frozen=True)
class DispatchResult:
    status: AcquisitionOutcome
    attempts: tuple[DispatchAttempt, ...]
    selected_route: RetrievalRoute | None = None
    error_category: str | None = None


@dataclass(frozen=True)
class RouteExecutionOutcome:
    status: AcquisitionOutcome
    acquired_count: int = 0
    error_category: str | None = None


class AttemptLedger(Protocol):
    def was_attempted(self, attempt_key: str) -> bool: ...
    def record(self, attempt: DispatchAttempt) -> None: ...


class InMemoryAttemptLedger:
    def __init__(self) -> None:
        self.attempts: dict[str, DispatchAttempt] = {}

    def was_attempted(self, attempt_key: str) -> bool:
        return attempt_key in self.attempts

    def record(self, attempt: DispatchAttempt) -> None:
        self.attempts[attempt.attempt_key] = attempt


RouteRunner = Callable[[RetrievalRoute, str], AcquisitionBatchResult | RouteExecutionOutcome]
GovernanceGuard = Callable[[RetrievalRoute, str], None]


class AdaptiveAcquisitionDispatcher:
    """Try permitted routes in cost order, once each, within an explicit bound."""

    def __init__(self, ledger: AttemptLedger) -> None:
        self.ledger = ledger

    def dispatch(
        self,
        recommendation: RetrievalRecommendation,
        *,
        runner: RouteRunner,
        governance_guard: GovernanceGuard,
        health: dict[RetrievalRoute, RouteHealth] | None = None,
        max_attempts: int = 3,
        operation_key: str,
    ) -> DispatchResult:
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or not 1 <= max_attempts <= 5
        ):
            raise ValueError("max_attempts must be an integer from 1 to 5")
        if recommendation.routes == (RetrievalRoute.REVIEW_REQUIRED,):
            return DispatchResult(
                AcquisitionOutcome.RESTRICTED, (), error_category="SOURCE_REVIEW_REQUIRED"
            )
        route_health = health or {}
        attempts: list[DispatchAttempt] = []
        routes = sorted(dict.fromkeys(recommendation.routes), key=lambda item: ROUTE_COST[item])
        for route in routes:
            if len(attempts) >= max_attempts:
                break
            state = route_health.get(route, RouteHealth())
            if state.circuit_open or not state.healthy:
                continue
            attempt_key = hashlib.sha256(
                f"{operation_key}\0{recommendation.url}\0{route.value}".encode()
            ).hexdigest()
            url_sha256 = hashlib.sha256(recommendation.url.encode("utf-8")).hexdigest()
            if self.ledger.was_attempted(attempt_key):
                continue
            try:
                governance_guard(route, recommendation.url)
            except PermissionError:
                attempt = DispatchAttempt(
                    attempt_key,
                    url_sha256,
                    route,
                    AcquisitionOutcome.RESTRICTED,
                    "ROUTE_NOT_PERMITTED",
                    0,
                )
                self.ledger.record(attempt)
                attempts.append(attempt)
                continue
            try:
                batch = runner(route, recommendation.url)
                acquired_count = (
                    len(batch.records)
                    if isinstance(batch, AcquisitionBatchResult)
                    else batch.acquired_count
                )
                attempt = DispatchAttempt(
                    attempt_key,
                    url_sha256,
                    route,
                    batch.status,
                    batch.error_category,
                    acquired_count,
                )
            except AcquisitionAdapterError as exc:
                attempt = DispatchAttempt(
                    attempt_key, url_sha256, route, exc.status, exc.error_category, 0
                )
            self.ledger.record(attempt)
            attempts.append(attempt)
            if attempt.status == AcquisitionOutcome.SUCCEEDED and attempt.acquired_count:
                return DispatchResult(attempt.status, tuple(attempts), route)
            if attempt.status == AcquisitionOutcome.PARTIAL:
                return DispatchResult(
                    attempt.status, tuple(attempts), route, attempt.error_category
                )
        if attempts:
            terminal = attempts[-1]
            return DispatchResult(
                terminal.status, tuple(attempts), error_category=terminal.error_category
            )
        return DispatchResult(
            AcquisitionOutcome.UNAVAILABLE, (), error_category="NO_EFFECTIVE_ROUTE"
        )
