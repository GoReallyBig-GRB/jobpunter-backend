"""Deterministic source governance and operational-state contracts."""


SOURCE_OPERATIONAL_STATES = frozenset({
    "AVAILABLE",
    "DEGRADED",
    "RESTRICTED",
    "FAILED",
    "UNAVAILABLE",
})
APPROVED_PERMISSION_REVIEW_STATUS = "APPROVED"


def has_approved_permission_review(source: object) -> bool:
    """Return whether the source has an explicit, auditable permission approval."""
    status = getattr(source, "permission_review_status", None)
    reference = getattr(source, "permission_review_reference", None)
    reviewed_at = getattr(source, "permission_reviewed_at", None)
    configuration = getattr(source, "configuration", None)
    review_details = (
        configuration.get("permission_review")
        if isinstance(configuration, dict)
        else None
    )
    required_evidence = ("access_method", "terms_reference", "rate_limit_policy", "rate_limit_reference")
    has_required_evidence = isinstance(review_details, dict) and all(
        isinstance(review_details.get(field), str) and review_details[field].strip()
        for field in required_evidence
    )
    return bool(
        isinstance(status, str)
        and status.strip().upper() == APPROVED_PERMISSION_REVIEW_STATUS
        and isinstance(reference, str)
        and reference.strip()
        and reviewed_at is not None
        and has_required_evidence
    )


def assert_automated_acquisition_permitted(source: object) -> None:
    """Fail closed unless terms/permission approval is explicit and auditable."""
    if not has_approved_permission_review(source):
        raise ValueError(
            "source permission review is not approved or lacks required evidence for automated acquisition"
        )


def validate_operational_state(state: str) -> str:
    """Return a normalized supported source operational state."""
    normalized = state.strip().upper() if isinstance(state, str) else ""
    if normalized not in SOURCE_OPERATIONAL_STATES:
        raise ValueError("unsupported source operational state")
    return normalized


def classify_acquisition_outcome(*, status: str, error_category: str | None = None) -> tuple[str, str | None]:
    """Map persisted acquisition outcomes to truthful source health states."""
    normalized = status.strip().upper() if isinstance(status, str) else ""
    if normalized == "SUCCEEDED":
        return "AVAILABLE", None
    if normalized in {"PARTIAL", "PARTIALLY_SUCCEEDED"}:
        return "DEGRADED", error_category or "PARTIAL_ACQUISITION"
    if normalized == "RESTRICTED":
        return "RESTRICTED", error_category or "SOURCE_RESTRICTED"
    if normalized == "UNAVAILABLE":
        return "UNAVAILABLE", error_category or "SOURCE_UNAVAILABLE"
    if normalized == "FAILED":
        return "FAILED", error_category or "ACQUISITION_FAILED"
    raise ValueError("unsupported acquisition outcome status")

def normalize_public_target_url(value: object, *, require_https: bool = False) -> str:
    """Normalize a registered HTTP(S) target and reject obvious unsafe URL forms.

    DNS resolution and redirect destinations must still be checked by the connector at
    execution time; this registration-time check is not a substitute for that enforcement.
    """
    import ipaddress
    import re
    from urllib.parse import parse_qsl, urlsplit, urlunsplit

    if not isinstance(value, str) or not value.strip():
        raise ValueError("target URL must be a non-empty string")
    raw = value.strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ValueError("target URL has an invalid port") from exc

    scheme = parts.scheme.lower()
    if scheme not in ({"https"} if require_https else {"http", "https"}):
        raise ValueError("target URL uses an unsupported scheme")
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("target URL must include a hostname and must not contain credentials")
    if parts.path and not parts.path.startswith("/"):
        raise ValueError("target URL path is invalid")
    if port not in (None, 80 if scheme == "http" else 443):
        raise ValueError("target URL must use a standard HTTP(S) port")

    hostname = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if not hostname or hostname == "localhost" or hostname.endswith((
        ".localhost", ".local", ".internal", ".lan", ".home.arpa", ".test", ".invalid"
    )):
        raise ValueError("target URL must not use a local or reserved hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("target URL must not use a private or non-public address")

    sensitive_markers = (
        "token", "apikey", "accesskey", "secret", "password",
        "signature", "credential", "authorization", "bearer",
    )
    for key, _ in parse_qsl(parts.query, keep_blank_values=True):
        normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
        if any(marker in normalized_key for marker in sensitive_markers):
            raise ValueError("target URL must not embed credentials in query parameters")

    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    rendered_port = "" if port is None or port == (80 if scheme == "http" else 443) else f":{port}"
    normalized_netloc = rendered_host + rendered_port
    return urlunsplit((scheme, normalized_netloc, parts.path or "/", parts.query, ""))

