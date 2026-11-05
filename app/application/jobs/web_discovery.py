"""Governed provider registry and measurable local/international coverage."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.infrastructure.db.models.source_intelligence import DiscoveryCoverageObservation
from app.ports.discovery import DiscoveredUrl, DiscoveryQuery, WebDiscoveryProvider


@dataclass(frozen=True)
class ProviderControl:
    enabled: bool = False
    approved: bool = False


@dataclass(frozen=True)
class CoverageMeasurement:
    provider_key: str
    query_fingerprint: str
    local_source_count: int
    international_source_count: int
    eligible_url_count: int
    total_url_count: int

    @property
    def coverage_ratio(self) -> float:
        return self.eligible_url_count / self.total_url_count if self.total_url_count else 0.0


class DiscoveryProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, WebDiscoveryProvider] = {}
        self._controls: dict[str, ProviderControl] = {}

    def register(self, provider: WebDiscoveryProvider, control: ProviderControl) -> None:
        key = provider.provider_key.strip().upper()
        if not key or key in self._providers:
            raise ValueError("discovery provider key must be unique")
        self._providers[key] = provider
        self._controls[key] = control

    def discover(
        self, provider_key: str, query: DiscoveryQuery, context: dict
    ) -> tuple[DiscoveredUrl, ...]:
        key = provider_key.strip().upper()
        provider = self._providers.get(key)
        control = self._controls.get(key)
        if provider is None or control is None:
            raise ValueError("discovery provider is not registered")
        if not control.enabled or not control.approved:
            raise PermissionError("discovery provider is not enabled and approved")
        if (
            provider.cost_tier.upper() == "PAID"
            and context.get("paid_provider_approved") is not True
        ):
            raise PermissionError("paid discovery provider requires explicit run approval")
        return provider.discover(query, context)


def measure_coverage(
    query: DiscoveryQuery, results: tuple[DiscoveredUrl, ...], provider_key: str
) -> CoverageMeasurement:
    domains_by_scope = {"LOCAL": set(), "INTERNATIONAL": set()}
    eligible = 0
    for item in results:
        scope = item.market_scope.strip().upper()
        if scope in domains_by_scope:
            domains_by_scope[scope].add(item.source_domain)
        if item.nigeria_eligible is True:
            eligible += 1
    fingerprint = hashlib.sha256(
        ("\0".join(query.terms) + f"\0{query.nigeria_eligible_only}\0{query.max_urls}").encode(
            "utf-8"
        )
    ).hexdigest()
    return CoverageMeasurement(
        provider_key=provider_key.strip().upper(),
        query_fingerprint=fingerprint,
        local_source_count=len(domains_by_scope["LOCAL"]),
        international_source_count=len(domains_by_scope["INTERNATIONAL"]),
        eligible_url_count=eligible,
        total_url_count=len(results),
    )


def persist_coverage_measurement(
    db: Session,
    measurement: CoverageMeasurement,
    *,
    details: dict | None = None,
) -> DiscoveryCoverageObservation:
    row = DiscoveryCoverageObservation(
        provider_key=measurement.provider_key,
        query_fingerprint=measurement.query_fingerprint,
        local_source_count=measurement.local_source_count,
        international_source_count=measurement.international_source_count,
        eligible_url_count=measurement.eligible_url_count,
        total_url_count=measurement.total_url_count,
        coverage_ratio=measurement.coverage_ratio,
        details=dict(details or {}),
    )
    db.add(row)
    db.flush()
    return row
