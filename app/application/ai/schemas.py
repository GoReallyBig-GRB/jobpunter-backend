from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskType = Literal[
    "document_parsing",
    "profile_extraction",
    "jd_extraction",
    "requirement_classification",
    "evidence_matching",
    "career_pathway_discovery",
    "cv_generation",
    "cover_letter",
    "application_questions",
    "suggested_answers",
    "claim_evidence_validation",
]


class GenerateOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    json_mode: bool = Field(
        default=False,
        alias="jsonMode",
        description="Compatibility flag; production tasks always use their server-owned output schema.",
    )
    max_tokens: int = Field(default=2048, ge=1, le=4096, alias="maxTokens")


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task: TaskType
    prompt: str = Field(min_length=1, max_length=80_000)
    schema_description: str | None = Field(
        default=None,
        max_length=12_000,
        alias="schemaDescription",
        description="Compatibility hint for structured mode; the server-owned task schema is authoritative.",
    )
    options: GenerateOptions = Field(default_factory=GenerateOptions)

    @field_validator("prompt")
    @classmethod
    def prompt_must_contain_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt must contain non-whitespace content.")
        return value

    @field_validator("schema_description")
    @classmethod
    def schema_description_must_contain_non_whitespace(
        cls, value: str | None
    ) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Schema description must contain non-whitespace content.")
        return value


class GenerateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raw_text: str = Field(alias="rawText")
    parsed_data: Any | None = Field(default=None, alias="parsedData")
    model_used: str = Field(alias="modelUsed")
    provider_used: str = Field(alias="providerUsed")
    latency_ms: int = Field(alias="latencyMs", ge=0)
    is_fallback: bool = Field(default=False, alias="isFallback")
    confidence: str = "MEDIUM"\n    execution_id: UUID | None = Field(default=None, alias="executionId")
