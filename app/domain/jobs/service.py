from dataclasses import dataclass

@dataclass(frozen=True)
class IdentityDecision:
    resolution_type:str
    confidence:float

def validate_identity_decision(decision:IdentityDecision)->None:
    if decision.resolution_type not in {"RESOLVE","MERGE","SPLIT","SUPERSEDE","REJECT"}:
        raise ValueError("unsupported resolution type")
    if not 0 <= decision.confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")