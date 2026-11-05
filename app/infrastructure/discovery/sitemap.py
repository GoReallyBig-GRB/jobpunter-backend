"""Zero-credential discovery from an approved domain's public sitemap."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit
from xml.etree import ElementTree

from app.application.jobs.source_governance import normalize_public_target_url
from app.application.jobs.source_intelligence import source_intelligence_is_executable
from app.ports.discovery import DiscoveredUrl, DiscoveryQuery

SitemapFetcher = Callable[[str, int], bytes]


class SitemapDiscoveryProvider:
    provider_key = "SITEMAP"
    cost_tier = "ZERO_COST"
    MAX_BYTES = 2_000_000
    MAX_URLS = 1_000

    def __init__(self, fetcher: SitemapFetcher) -> None:
        self.fetcher = fetcher

    def discover(self, query: DiscoveryQuery, context: dict) -> tuple[DiscoveredUrl, ...]:
        profile = context.get("source_intelligence")
        if profile is None or not source_intelligence_is_executable(profile):
            raise PermissionError("sitemap discovery requires approved source intelligence")
        sitemap_url = normalize_public_target_url(context.get("sitemap_url"))
        sitemap_domain = urlsplit(sitemap_url).hostname or ""
        if sitemap_domain != profile.domain:
            raise PermissionError("sitemap URL must use the approved source domain")
        body = self.fetcher(sitemap_url, self.MAX_BYTES)
        if len(body) > self.MAX_BYTES:
            raise ValueError("sitemap response exceeded configured byte limit")
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ValueError("sitemap response is invalid XML") from exc
        configured_scope = str(context.get("market_scope", "INTERNATIONAL")).strip().upper()
        if configured_scope not in {"LOCAL", "INTERNATIONAL"}:
            raise ValueError("market_scope must be LOCAL or INTERNATIONAL")
        nigeria_eligible = context.get("nigeria_eligible")
        if nigeria_eligible not in {True, False, None}:
            raise ValueError("nigeria_eligible must be true, false, or null")
        limit = min(query.max_urls, self.MAX_URLS)
        discovered: list[DiscoveredUrl] = []
        terms = tuple(term.casefold() for term in query.terms if term.strip())
        for node in root.iter():
            if not node.tag.lower().endswith("loc") or not node.text:
                continue
            try:
                url = normalize_public_target_url(node.text.strip())
            except ValueError:
                continue
            if (urlsplit(url).hostname or "") != profile.domain:
                continue
            if terms and not any(term in url.casefold() for term in terms):
                continue
            if query.nigeria_eligible_only and nigeria_eligible is False:
                continue
            discovered.append(
                DiscoveredUrl(
                    url=url,
                    provider_key=self.provider_key,
                    source_domain=profile.domain,
                    market_scope=configured_scope,
                    nigeria_eligible=nigeria_eligible,
                    evidence=("approved_sitemap",),
                )
            )
            if len(discovered) >= limit:
                break
        return tuple(discovered)
