"""Replaceable web-discovery provider contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DiscoveryQuery:
    terms: tuple[str, ...]
    nigeria_eligible_only: bool = True
    max_urls: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.terms, tuple) or any(
            not isinstance(term, str) for term in self.terms
        ):
            raise TypeError("discovery terms must be a tuple of strings")
        if any(not term.strip() for term in self.terms):
            raise ValueError("discovery terms must not contain blank strings")
        if not isinstance(self.nigeria_eligible_only, bool):
            raise TypeError("nigeria_eligible_only must be a boolean")
        if not isinstance(self.max_urls, int) or isinstance(self.max_urls, bool):
            raise TypeError("max_urls must be an integer")
        if not 1 <= self.max_urls <= 1_000:
            raise ValueError("max_urls must be an integer from 1 to 1000")


@dataclass(frozen=True)
class DiscoveredUrl:
    url: str
    provider_key: str
    source_domain: str
    market_scope: str
    nigeria_eligible: bool | None
    evidence: tuple[str, ...] = ()


class WebDiscoveryProvider(Protocol):
    provider_key: str
    cost_tier: str

    def discover(self, query: DiscoveryQuery, context: dict) -> tuple[DiscoveredUrl, ...]: ...
