"""Server-owned structured-output contracts for safety-sensitive AI tasks.

Contracts are intentionally enforced in application code rather than relying on
provider JSON mode or caller-supplied schema descriptions.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class EvidenceStatus(StrEnum):
    DIRECT = "direct"
    TRANSFERABLE = "transferable"
    PARTIAL = "partial"
    MISSING = "missing"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"


NonBlank = Annotated[str, Field(min_length=1, max_length=12_000)]


class EvidenceMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement_id: NonBlank
    requirement_text: NonBlank
    status: EvidenceStatus
    rationale: NonBlank
    evidence_refs: list[NonBlank] = Field(max_length=100)


class EvidenceMatchingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[EvidenceMatch] = Field(min_length=1, max_length=500)


_TASK_OUTPUT_ADAPTERS: dict[str, TypeAdapter] = {
    "evidence_matching": TypeAdapter(EvidenceMatchingOutput),
}


def validate_task_output(task: str, parsed_data: object) -> object:
    """Validate known task-specific output contracts; unknown contracts fail closed only for that task."""
    adapter = _TASK_OUTPUT_ADAPTERS.get(task)
    if adapter is None:
        return parsed_data
    try:
        return adapter.validate_python(parsed_data, strict=True)
    except ValidationError as exc:
        raise InvalidTaskOutput(task) from exc


class InvalidTaskOutput(ValueError):
    """Provider output violates the server-owned contract for its AI task."""
