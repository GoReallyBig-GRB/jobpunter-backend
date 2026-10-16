"""Strict request contracts for candidate-owned CV Studio operations."""
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class MasterCVRegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_profile_revision: int = Field(ge=1)


class CVVariantCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cv_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=200)
    source_master_version_id: UUID
    content: dict[str, Any] | None = None


class CVVersionSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_latest_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: dict[str, Any]


class CVVersionApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str | None = Field(default=None, max_length=2000)
