"""Deterministic source governance and operational-state contracts."""

from collections.abc import Iterable


SOURCE_OPERATIONAL_STATES = frozenset({
    "AVAILABLE",
    "DEGRADED",
    "RESTRICTED",
    "FAILED",
    "UNAVAILABLE",
})
APPROVED_PERMISSION_REVIEW_STATUS = "APPROVED"


def assert_automated_acquisition_permitted(source: object) -> None:
    """Fail closed unless terms/permission approval is explicit and auditable."""
    status = getattr(source, "permission_review_status", None)
    reference = getattr(source, "permission_review_reference", None)
    reviewed_at = getattr(source, "permission_reviewed_at", None)
    if (
        not isinstance(status, str)
        or status.strip().upper() != APPROVED_PERMISSION_REVIEW_STATUS
        or not isinstance(reference, str)
        or not reference.strip()
        or reviewed_at is None
    ):
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
    if normalized == "FAILED":
        return "FAILED", error_category or "ACQUISITION_FAILED"
    if normalized in {"PENDING", "RUNNING"}:
        return "UNAVAILABLE", None
    raise ValueError("unsupported acquisition outcome status")
