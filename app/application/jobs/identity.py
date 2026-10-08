from dataclasses import dataclass

@dataclass(frozen=True)
class IdentityCandidate:
    job_id: str
    confidence: float
    method: str

def validate_identity_candidates(candidates: list[IdentityCandidate]) -> None:
    for candidate in candidates:
        if not 0 <= candidate.confidence <= 1:
            raise ValueError("Identity confidence must be between 0 and 1")
    if len(candidates) > 1:
        ordered = sorted(candidates, key=lambda item: item.confidence, reverse=True)
        if ordered[0].confidence == ordered[1].confidence:
            raise ValueError("Ambiguous identity candidates require review")
