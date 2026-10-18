"""Deterministic matching for persisted forbidden-target rules."""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from app.application.jobs.source_governance import normalize_public_target_url


RULE_TYPES = frozenset({"HOST_EXACT", "HOST_SUFFIX", "URL_EXACT", "URL_PATH_PREFIX"})
CONNECTOR_SCOPES = frozenset({
    "ALL", "SCRAPY", "PLAYWRIGHT", "JSON_API", "RSS_ATOM", "JOBSPY", "GREENHOUSE_JOB_BOARD",
})


class ForbiddenTargetError(ValueError):
    """A target matched an active, administrator-managed deny rule."""

    def __init__(self, rule_id: str, reason: str):
        self.rule_id = rule_id
        self.reason = reason
        super().__init__("target is blocked by an active forbidden-target rule")


def _normalize_host(value: str) -> str:
    host = value.strip().rstrip(".")
    if not host or any(char in host for char in "/?#@"):
        raise ValueError("host rule must contain only a hostname")
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("host rule contains an invalid hostname") from exc


def normalize_forbidden_rule(rule_type: str, pattern: str) -> str:
    kind = rule_type.strip().upper()
    if kind not in RULE_TYPES:
        raise ValueError("unsupported forbidden-target rule type")
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("forbidden-target pattern must not be blank")
    value = pattern.strip()
    if kind in {"HOST_EXACT", "HOST_SUFFIX"}:
        if value.startswith("*."):
            value = value[2:]
        if value.startswith("."):
            value = value[1:]
        host = _normalize_host(value)
        if host in {"localhost"} or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
            # It is useful to explicitly block local names too; network safety remains a
            # separate, higher-precedence control.
            return host
        return host

    try:
        parsed = urlsplit(value)
        normalized = normalize_public_target_url(value)
        normalized_parts = urlsplit(normalized)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("URL rule must be a valid HTTP(S) URL") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL rules must not contain credentials")
    if kind == "URL_PATH_PREFIX" and normalized_parts.query:
        raise ValueError("path-prefix rules must not contain query parameters")
    if kind == "URL_PATH_PREFIX":
        return normalized_parts._replace(query="", fragment="").geturl().rstrip("/") or "/"
    return normalized


def _normalized_request_url(value: str) -> tuple[str, str, str, str]:
    normalized = normalize_public_target_url(value)
    parsed = urlsplit(normalized)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("target is not publicly routable")
    host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    port = parsed.port
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != default_port:
        netloc += f":{port}"
    return parsed.scheme.lower(), netloc, host, parsed.path or "/"


def matching_forbidden_rule(
    url: str,
    rules: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    connector_type: str,
) -> dict[str, Any] | None:
    """Return the first matching active rule; order is deterministic by rule ID."""
    scheme, netloc, host, path = _normalized_request_url(url)
    normalized_url = f"{scheme}://{netloc}{path}"
    # Keep the query string for URL_EXACT matching; never discard job identifiers or
    # pagination parameters when deciding whether a target is explicitly forbidden.
    normalized_url = normalize_public_target_url(url)
    connector = connector_type.strip().upper()
    for rule in sorted(rules, key=lambda item: str(item.get("id", ""))):
        if not rule.get("active", True):
            continue
        scope = str(rule.get("connector_scope", "ALL")).upper()
        if scope not in {"ALL", connector}:
            continue
        kind = str(rule.get("rule_type", "")).upper()
        pattern = str(rule.get("pattern", ""))
        if kind == "HOST_EXACT" and host == pattern:
            return rule
        if kind == "HOST_SUFFIX" and (host == pattern or host.endswith("." + pattern)):
            return rule
        if kind == "URL_EXACT" and normalized_url == pattern:
            return rule
        if kind == "URL_PATH_PREFIX":
            rule_parts = urlsplit(pattern)
            rule_host = (rule_parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
            rule_scheme = rule_parts.scheme.lower()
            rule_netloc = rule_parts.netloc.lower()
            rule_path = rule_parts.path.rstrip("/") or "/"
            # A URL path-prefix rule is scoped to the normalized origin as well
            # as the path. Do not silently broaden an HTTPS rule to HTTP or
            # another origin/port.
            if (
                scheme == rule_scheme
                and netloc.lower() == rule_netloc
                and host == rule_host
                and (
                    path == rule_path
                    or path.startswith(rule_path + "/")
                    or rule_path == "/"
                )
            ):
                return rule
    return None


def assert_target_not_forbidden(
    url: str,
    rules: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    connector_type: str,
) -> None:
    match = matching_forbidden_rule(url, rules, connector_type=connector_type)
    if match is not None:
        raise ForbiddenTargetError(str(match.get("id", "")), str(match.get("reason", "")))


def assert_current_target_not_forbidden(
    url: str,
    context: dict[str, Any],
    *,
    connector_type: str,
    method: str = "GET",
) -> None:
    """Revalidate a request against live policy when registered acquisition supplies it."""
    revalidator = context.get("acquisition_policy_revalidator")
    if callable(revalidator):
        revalidator(url=url, connector_type=connector_type, method=method)
        return
    rules = context.get("forbidden_target_rules", [])
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise ValueError("forbidden target policy is invalid")
    assert_target_not_forbidden(url, rules, connector_type=connector_type)
