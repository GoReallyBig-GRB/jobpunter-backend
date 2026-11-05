"""Classify discovered URLs without assuming a finite source universe."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from app.application.jobs.source_governance import normalize_public_target_url


class SourceKind(StrEnum):
    KNOWN_ATS = "KNOWN_ATS"
    RSS_ATOM = "RSS_ATOM"
    JSON_JOB_API = "JSON_JOB_API"
    JSON_LD = "JSON_LD"
    APPROVED_STATIC_HTML = "APPROVED_STATIC_HTML"
    JAVASCRIPT_RENDERED = "JAVASCRIPT_RENDERED"
    AGGREGATOR = "AGGREGATOR"
    UNKNOWN_REVIEW_REQUIRED = "UNKNOWN_REVIEW_REQUIRED"


class RetrievalRoute(StrEnum):
    JSON_API = "JSON_API"
    RSS_ATOM = "RSS_ATOM"
    PUBLIC_HTTP = "PUBLIC_HTTP"
    SCRAPY = "SCRAPY"
    PLAYWRIGHT = "PLAYWRIGHT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class ResolutionEvidence:
    code: str
    value: str


@dataclass(frozen=True)
class ResolutionSignals:
    content_type: str | None = None
    body_sample: str | None = None
    approved_static_html: bool = False
    javascript_required: bool = False
    aggregator_domains: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RetrievalRecommendation:
    url: str
    domain: str
    source_kind: SourceKind
    routes: tuple[RetrievalRoute, ...]
    confidence: float
    evidence: tuple[ResolutionEvidence, ...]


@dataclass(frozen=True)
class AtsPattern:
    name: str
    host_suffix: str
    path_pattern: str
    routes: tuple[RetrievalRoute, ...]


DEFAULT_ATS_PATTERNS = (
    AtsPattern(
        "greenhouse",
        "boards-api.greenhouse.io",
        r"^/v1/boards/[^/]+/jobs",
        (RetrievalRoute.JSON_API,),
    ),
    AtsPattern(
        "greenhouse",
        "boards.greenhouse.io",
        r"^/[^/]+",
        (RetrievalRoute.PUBLIC_HTTP, RetrievalRoute.SCRAPY, RetrievalRoute.PLAYWRIGHT),
    ),
    AtsPattern("lever", "api.lever.co", r"^/v0/postings/", (RetrievalRoute.JSON_API,)),
    AtsPattern(
        "lever",
        "jobs.lever.co",
        r"^/[^/]+",
        (RetrievalRoute.PUBLIC_HTTP, RetrievalRoute.SCRAPY, RetrievalRoute.PLAYWRIGHT),
    ),
    AtsPattern(
        "smartrecruiters", "api.smartrecruiters.com", r"^/v1/companies/", (RetrievalRoute.JSON_API,)
    ),
    AtsPattern(
        "smartrecruiters",
        "jobs.smartrecruiters.com",
        r"^/[^/]+",
        (RetrievalRoute.PUBLIC_HTTP, RetrievalRoute.SCRAPY, RetrievalRoute.PLAYWRIGHT),
    ),
)


class UrlSourceResolver:
    """Resolve URLs from extensible patterns plus response evidence."""

    def __init__(self, ats_patterns: tuple[AtsPattern, ...] = DEFAULT_ATS_PATTERNS) -> None:
        self.ats_patterns = ats_patterns

    def resolve(
        self,
        url: str,
        signals: ResolutionSignals | None = None,
    ) -> RetrievalRecommendation:
        normalized = normalize_public_target_url(url)
        parts = urlsplit(normalized)
        domain = parts.hostname or ""
        signals = signals or ResolutionSignals()
        content_type = (signals.content_type or "").split(";", 1)[0].strip().lower()
        body = (signals.body_sample or "")[:262_144]

        for pattern in self.ats_patterns:
            if (
                domain == pattern.host_suffix or domain.endswith("." + pattern.host_suffix)
            ) and re.search(pattern.path_pattern, parts.path, re.IGNORECASE):
                return self._result(
                    normalized,
                    domain,
                    SourceKind.KNOWN_ATS,
                    pattern.routes,
                    0.99,
                    ResolutionEvidence("ATS_PATTERN", pattern.name),
                )

        if self._is_feed(parts.path, content_type, body):
            return self._result(
                normalized,
                domain,
                SourceKind.RSS_ATOM,
                (RetrievalRoute.RSS_ATOM,),
                0.96,
                ResolutionEvidence("FEED_SIGNAL", content_type or parts.path),
            )
        if self._is_json_job_api(content_type, body):
            return self._result(
                normalized,
                domain,
                SourceKind.JSON_JOB_API,
                (RetrievalRoute.JSON_API,),
                0.92,
                ResolutionEvidence("JSON_JOB_SHAPE", content_type or "body"),
            )
        if self._has_jobposting_json_ld(body):
            return self._result(
                normalized,
                domain,
                SourceKind.JSON_LD,
                (RetrievalRoute.PUBLIC_HTTP, RetrievalRoute.SCRAPY, RetrievalRoute.PLAYWRIGHT),
                0.94,
                ResolutionEvidence("JSON_LD_TYPE", "JobPosting"),
            )
        aggregator_domains = {item.lower().lstrip(".") for item in signals.aggregator_domains}
        if any(domain == item or domain.endswith("." + item) for item in aggregator_domains):
            return self._result(
                normalized,
                domain,
                SourceKind.AGGREGATOR,
                (RetrievalRoute.PUBLIC_HTTP, RetrievalRoute.SCRAPY, RetrievalRoute.PLAYWRIGHT),
                0.90,
                ResolutionEvidence("APPROVED_AGGREGATOR_PATTERN", domain),
            )
        if signals.approved_static_html:
            return self._result(
                normalized,
                domain,
                SourceKind.APPROVED_STATIC_HTML,
                (RetrievalRoute.PUBLIC_HTTP, RetrievalRoute.SCRAPY, RetrievalRoute.PLAYWRIGHT),
                0.85,
                ResolutionEvidence("STATIC_HTML_APPROVED", domain),
            )
        if signals.javascript_required:
            return self._result(
                normalized,
                domain,
                SourceKind.JAVASCRIPT_RENDERED,
                (RetrievalRoute.PLAYWRIGHT,),
                0.80,
                ResolutionEvidence("JAVASCRIPT_REQUIRED", domain),
            )
        return self._result(
            normalized,
            domain,
            SourceKind.UNKNOWN_REVIEW_REQUIRED,
            (RetrievalRoute.REVIEW_REQUIRED,),
            0.0,
            ResolutionEvidence("NO_APPROVED_CAPABILITY_MATCH", domain),
        )

    @staticmethod
    def _result(
        url: str,
        domain: str,
        kind: SourceKind,
        routes: tuple[RetrievalRoute, ...],
        confidence: float,
        *evidence: ResolutionEvidence,
    ) -> RetrievalRecommendation:
        return RetrievalRecommendation(url, domain, kind, routes, confidence, tuple(evidence))

    @staticmethod
    def _is_feed(path: str, content_type: str, body: str) -> bool:
        if content_type in {"application/rss+xml", "application/atom+xml"}:
            return True
        if path.lower().endswith((".rss", ".atom", "/feed", "/rss", "/atom")):
            return True
        stripped = body.lstrip()[:256].lower()
        return stripped.startswith("<?xml") and (
            "<rss" in body[:2048].lower() or "<feed" in body[:2048].lower()
        )

    @staticmethod
    def _is_json_job_api(content_type: str, body: str) -> bool:
        if content_type not in {"application/json", "application/ld+json"} or not body:
            return False
        try:
            payload: Any = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        samples = payload if isinstance(payload, list) else [payload]
        if isinstance(payload, dict):
            for key in ("jobs", "results", "data", "items"):
                if isinstance(payload.get(key), list):
                    samples = payload[key]
                    break
        return any(
            isinstance(item, dict)
            and any(key in item for key in ("title", "job_title", "position"))
            and any(key in item for key in ("url", "job_url", "apply_url", "id"))
            for item in samples[:10]
        )

    @staticmethod
    def _has_jobposting_json_ld(body: str) -> bool:
        return bool(re.search(r'"@type"\s*:\s*"JobPosting"', body, re.IGNORECASE))
