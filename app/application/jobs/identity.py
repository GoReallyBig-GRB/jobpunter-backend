from dataclasses import dataclass
from enum import StrEnum


class ResolutionStatus(StrEnum):
    NEW = "NEW"
    MATCHED = "MATCHED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


@dataclass(frozen=True)
class IdentityCandidate:
    job_id: str
    confidence: float
    method: str


@dataclass(frozen=True)
class IdentityDecision:
    status: ResolutionStatus
    job_id: str | None
    method: str
    confidence: float
    candidate_job_ids: tuple[str, ...] = ()


def validate_identity_candidates(candidates: list[IdentityCandidate]) -> None:
    for candidate in candidates:
        if not 0 <= candidate.confidence <= 1:
            raise ValueError("Identity confidence must be between 0 and 1")
    ids = [candidate.job_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("Identity candidates must have unique job IDs")


def decide_identity(
    candidates: list[IdentityCandidate],
    *,
    match_threshold: float = 0.98,
    ambiguity_margin: float = 0.05,
) -> IdentityDecision:
    """Resolve only high-confidence deterministic matches; never guess on weak similarity."""
    validate_identity_candidates(candidates)
    if not 0 < match_threshold <= 1:
        raise ValueError("match_threshold must be between 0 and 1")
    if not 0 <= ambiguity_margin < 1:
        raise ValueError("ambiguity_margin must be between 0 and 1")

    if not candidates:
        return IdentityDecision(ResolutionStatus.NEW, None, "NO_MATCH", 0.0)

    ranked = sorted(candidates, key=lambda item: (-item.confidence, item.job_id))
    top = ranked[0]
    if top.confidence < match_threshold:
        return IdentityDecision(
            ResolutionStatus.NEW, None, "NO_CONFIDENT_MATCH", top.confidence,
            tuple(candidate.job_id for candidate in ranked),
        )

    close = [
        candidate for candidate in ranked
        if top.confidence - candidate.confidence < ambiguity_margin
    ]
    if len(close) > 1:
        return IdentityDecision(
            ResolutionStatus.REQUIRES_REVIEW, None, "AMBIGUOUS_MATCH",
            top.confidence, tuple(candidate.job_id for candidate in close),
        )

    return IdentityDecision(
        ResolutionStatus.MATCHED, top.job_id, top.method, top.confidence,
        (top.job_id,),
    )
