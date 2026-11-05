"""Execute a resolved URL through approved registered connectors and durable dispatch."""

from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

from app.application.jobs.adaptive_dispatcher import (
    AdaptiveAcquisitionDispatcher,
    DispatchResult,
    RouteExecutionOutcome,
    RouteHealth,
)
from app.application.jobs.discovery_intake import discovery_candidate_is_executable
from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
from app.application.jobs.source_governance import normalize_public_target_url
from app.application.jobs.source_intelligence import source_intelligence_is_executable
from app.application.jobs.url_source_resolver import (
    ResolutionEvidence,
    ResolutionSignals,
    RetrievalRecommendation,
    RetrievalRoute,
    SourceKind,
    UrlSourceResolver,
)
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome
from app.ports.repositories.acquisition import AcquisitionRepository

ROUTE_CONNECTOR_TYPES = {
    RetrievalRoute.JSON_API: "JSON_API",
    RetrievalRoute.RSS_ATOM: "RSS_ATOM",
    RetrievalRoute.PUBLIC_HTTP: "PUBLIC_HTTP",
    RetrievalRoute.SCRAPY: "SCRAPY",
    RetrievalRoute.PLAYWRIGHT: "PLAYWRIGHT",
}


def _route_context(route: RetrievalRoute, url: str) -> dict:
    return {
        RetrievalRoute.JSON_API: {"endpoint_url": url},
        RetrievalRoute.RSS_ATOM: {"feed_url": url},
        RetrievalRoute.PUBLIC_HTTP: {"page_url": url},
        RetrievalRoute.SCRAPY: {"start_urls": [url]},
        RetrievalRoute.PLAYWRIGHT: {"start_urls": [url]},
    }[route]


class ResolvedUrlAcquisitionService:
    def __init__(
        self,
        repository: AcquisitionRepository,
        dispatcher: AdaptiveAcquisitionDispatcher,
        resolver: UrlSourceResolver | None = None,
    ) -> None:
        self.repository = repository
        self.dispatcher = dispatcher
        self.resolver = resolver or UrlSourceResolver()

    def execute(
        self,
        *,
        source_id: UUID,
        profile: object,
        operation_key: str,
        url: str,
        connector_ids: dict[RetrievalRoute, UUID],
        signals: ResolutionSignals | None = None,
        recommendation: RetrievalRecommendation | None = None,
        health: dict[RetrievalRoute, RouteHealth] | None = None,
        max_attempts: int = 3,
    ) -> DispatchResult:
        recommendation = recommendation or self.resolver.resolve(url, signals)
        normalized_url = normalize_public_target_url(url)
        if recommendation.url != normalized_url:
            raise ValueError("retrieval recommendation does not match the requested URL")
        source = self.repository.get_source(source_id)
        if source is None:
            raise ValueError("source does not exist")
        source_base_url = getattr(source, "base_url", None)
        source_domain = (
            urlsplit(normalize_public_target_url(source_base_url)).hostname
            if isinstance(source_base_url, str) and source_base_url.strip()
            else None
        )
        if (
            source_domain != recommendation.domain
            or getattr(profile, "domain", None) != recommendation.domain
        ):
            raise PermissionError("resolved URL domain is not bound to the approved source")

        approved_routes = set((getattr(profile, "capabilities", None) or {}).get("routes", []))

        def governance_guard(route: RetrievalRoute, _url: str) -> None:
            if not source_intelligence_is_executable(profile):
                raise PermissionError("source intelligence is not approved")
            if route.value not in approved_routes:
                raise PermissionError("retrieval route is not approved for this source")

        def runner(route: RetrievalRoute, target_url: str) -> RouteExecutionOutcome:
            connector_id = connector_ids.get(route)
            if connector_id is None:
                raise AcquisitionAdapterError(
                    AcquisitionOutcome.UNAVAILABLE, "ROUTE_NOT_CONFIGURED"
                )
            connector = self.repository.get_connector(connector_id)
            expected_type = ROUTE_CONNECTOR_TYPES[route]
            if (
                connector is None
                or connector.source_id != source_id
                or str(connector.connector_type).strip().upper() != expected_type
            ):
                raise AcquisitionAdapterError(
                    AcquisitionOutcome.RESTRICTED, "ROUTE_CONNECTOR_MISMATCH"
                )
            result = RegisteredAcquisitionService(self.repository).run(
                source_id,
                connector_id,
                f"resolved-url:{operation_key}:{route.value}",
                _route_context(route, target_url),
            )
            return RouteExecutionOutcome(
                status=AcquisitionOutcome(result.status),
                acquired_count=result.acquired_count,
                error_category=result.error_category,
            )

        return self.dispatcher.dispatch(
            recommendation,
            runner=runner,
            governance_guard=governance_guard,
            health=health,
            max_attempts=max_attempts,
            operation_key=operation_key,
        )

    def execute_candidate(
        self,
        *,
        source_id: UUID,
        profile: object,
        candidate: object,
        operation_key: str,
        connector_ids: dict[RetrievalRoute, UUID],
        health: dict[RetrievalRoute, RouteHealth] | None = None,
        max_attempts: int = 3,
    ) -> DispatchResult:
        if not discovery_candidate_is_executable(profile, candidate):
            raise PermissionError("discovery candidate is not approved")
        if getattr(candidate, "profile_id", None) != getattr(profile, "id", None):
            raise PermissionError("discovery candidate is not bound to this source profile")
        url = getattr(candidate, "normalized_url", None)
        if not isinstance(url, str) or not url.strip():
            raise ValueError("discovery candidate URL is invalid")
        try:
            recommendation = RetrievalRecommendation(
                url=normalize_public_target_url(url),
                domain=urlsplit(normalize_public_target_url(url)).hostname or "",
                source_kind=SourceKind(getattr(candidate, "source_kind", "")),
                routes=tuple(
                    RetrievalRoute(route) for route in getattr(candidate, "recommended_routes", [])
                ),
                confidence=float(getattr(candidate, "confidence", 0.0)),
                evidence=tuple(
                    ResolutionEvidence(str(item["code"]), str(item["value"]))
                    for item in getattr(candidate, "evidence", [])
                    if isinstance(item, dict) and "code" in item and "value" in item
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("discovery candidate recommendation is invalid") from exc
        if not recommendation.routes:
            raise ValueError("discovery candidate recommendation has no routes")
        return self.execute(
            source_id=source_id,
            profile=profile,
            operation_key=operation_key,
            url=url,
            connector_ids=connector_ids,
            recommendation=recommendation,
            health=health,
            max_attempts=max_attempts,
        )
