"""Fail-closed sitemap discovery placeholder pending an approved requirement."""

from __future__ import annotations

from app.ports.discovery import DiscoveredUrl, DiscoveryQuery


class SitemapDiscoveryProvider:
    provider_key = "SITEMAP"
    cost_tier = "ZERO_COST"
    enabled = False

    def discover(self, query: DiscoveryQuery, context: dict) -> tuple[DiscoveredUrl, ...]:
        raise PermissionError(
            "sitemap discovery is disabled because no production requirement is present "
            "in the approved PRD or technical specification"
        )
