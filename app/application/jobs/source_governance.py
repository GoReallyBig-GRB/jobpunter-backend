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
    return bool(
        isinstance(status, str)
        and status.strip().upper() == APPROVED_PERMISSION_REVIEW_STATUS
        and isinstance(reference, str)
        and reference.strip()
        and reviewed_at is not None
    )


def assert_automated_acquisition_permitted(source: object) -> None:
    """Fail closed unless terms/permission approval is explicit and auditable."""
    if not has_approved_permission_review(source):
        raise ValueError("source permission review is not approved for automated acquisition")


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
    if normalized in {"PENDING", "RUNNING"}:
        return "UNAVAILABLE", None
    raise ValueError("unsupported acquisition outcome status")
