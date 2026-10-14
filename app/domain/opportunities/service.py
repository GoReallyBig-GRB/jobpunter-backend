from datetime import datetime, timezone


APPLICATION_TRANSITIONS = {
    "PLANNED": {"APPROVED", "WITHDRAWN", "CLOSED"},
    "APPROVED": {"SUBMITTED", "WITHDRAWN", "CLOSED"},
    "SUBMITTED": {"INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "CLOSED"},
    "INTERVIEW": {"OFFER", "REJECTED", "WITHDRAWN", "CLOSED"},
    "OFFER": {"ACCEPTED", "REJECTED", "WITHDRAWN", "CLOSED"},
    "ACCEPTED": set(),
    "REJECTED": set(),
    "WITHDRAWN": set(),
    "CLOSED": set(),
}


def qualify_punt(evaluation) -> None:
    """Apply the separate Punt qualification gate to a candidate/job evaluation."""
    if evaluation.validity_status != "CURRENT":
        raise ValueError("Only current evaluations can qualify a Punt")
    if evaluation.outcome != "APPLY":
        raise ValueError("Evaluation outcome does not qualify for a Punt")
    if evaluation.hard_blockers:
        raise ValueError("Evaluation has hard blockers and cannot qualify for a Punt")
    if evaluation.score is None:
        raise ValueError("Punt qualification requires a scored evaluation")
    summary = evaluation.evidence_summary or {}
    dimensions = summary.get("dimensions") or {}
    if dimensions.get("deterministic_eligibility") != "ELIGIBLE":
        raise ValueError("Punt qualification requires deterministic eligibility to pass")
    if dimensions.get("mandatory_requirements") != "MET":
        raise ValueError("Punt qualification requires all mandatory requirements to be supported")
    if dimensions.get("application_accessibility") != "VERIFIED_ACTIVE":
        raise ValueError("Punt qualification requires a verified application route")
    if dimensions.get("job_freshness") not in {"FRESH", "CURRENT"}:
        raise ValueError("Punt qualification requires a current job freshness status")


def transition_application(
    application,
    target_status: str,
    *,
    actor_type: str,
    reason: str,
    notes: str | None = None,
    now=None,
):
    """Apply one canonical candidate-visible lifecycle transition.

    Real-world milestones are candidate-recorded in the current MVP. A verified
    integration path may be added separately; system actions cannot assert them.
    """
    target_status = str(target_status).strip().upper()
    allowed = APPLICATION_TRANSITIONS.get(application.status)
    if allowed is None or target_status not in allowed:
        raise ValueError(f"Invalid application transition: {application.status} -> {target_status}")
    if actor_type != "USER":
        raise ValueError("Application lifecycle transitions require an authenticated user action")
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise ValueError("A transition reason of at least 3 characters is required")
    current_time = now or datetime.now(timezone.utc)
    previous = application.status
    application.status = target_status
    application.revision += 1
    application.updated_at = current_time
    if target_status == "SUBMITTED":
        application.submitted_at = current_time
    return {
        "event_type": "APPLICATION_STATUS_CHANGED",
        "from_status": previous,
        "to_status": target_status,
        "actor_type": actor_type,
        "occurred_at": current_time,
        "reason": reason.strip(),
        "notes": notes.strip() if notes and notes.strip() else None,
    }
