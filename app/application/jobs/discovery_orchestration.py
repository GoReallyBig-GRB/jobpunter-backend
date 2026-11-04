"""Strict schemas and capability checks for discovery orchestration.

Admin configuration selects only server-owned tool keys and declarative policy.
It cannot introduce executable code or unsupported connector capabilities.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DiscoveryToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_key: str
    tool_type: str
    adapter_key: str
    capabilities: frozenset[str]
    availability: Literal["AVAILABLE", "POLICY_GATED"] = "AVAILABLE"


DISCOVERY_TOOL_CATALOG: dict[str, DiscoveryToolDefinition] = {
    "GREENHOUSE_JOB_BOARD": DiscoveryToolDefinition(
        tool_key="GREENHOUSE_JOB_BOARD", tool_type="ATS", adapter_key="GREENHOUSE_JOB_BOARD",
        capabilities=frozenset({"structured_acquisition", "job_detail_retrieval", "api", "retryable_acquisition"}),
    ),
    "JSON_API": DiscoveryToolDefinition(
        tool_key="JSON_API", tool_type="API", adapter_key="JSON_API",
        capabilities=frozenset({"structured_acquisition", "job_detail_retrieval", "api", "retryable_acquisition"}),
    ),
    "RSS_ATOM": DiscoveryToolDefinition(
        tool_key="RSS_ATOM", tool_type="FEED", adapter_key="RSS_ATOM",
        capabilities=frozenset({"structured_acquisition", "feed", "source_revalidation", "retryable_acquisition"}),
    ),
    "SCRAPY": DiscoveryToolDefinition(
        tool_key="SCRAPY", tool_type="STATIC_CRAWLER", adapter_key="SCRAPY",
        capabilities=frozenset({"static_retrieval", "job_detail_retrieval", "retryable_acquisition"}),
    ),
    "PLAYWRIGHT": DiscoveryToolDefinition(
        tool_key="PLAYWRIGHT", tool_type="BROWSER_AUTOMATION", adapter_key="PLAYWRIGHT",
        capabilities=frozenset({"browser_automation", "javascript_rendering", "job_detail_retrieval", "retryable_acquisition"}),
    ),
    "JOBSPY": DiscoveryToolDefinition(
        tool_key="JOBSPY", tool_type="SEARCH_PROVIDER", adapter_key="JOBSPY",
        capabilities=frozenset({"broad_job_search", "structured_acquisition", "retryable_acquisition"}),
        availability="POLICY_GATED",
    ),
    "INTERNAL_RECORD_VALIDATOR": DiscoveryToolDefinition(
        tool_key="INTERNAL_RECORD_VALIDATOR", tool_type="INTERNAL_SERVICE",
        adapter_key="INTERNAL_RECORD_VALIDATOR",
        capabilities=frozenset({"record_validation", "normalization", "completeness_assessment"}),
    ),
}


ROLE_CAPABILITY_CONTRACTS: dict[str, frozenset[str]] = {
    "SHARED_POOL_FRESHNESS": frozenset({"source_revalidation", "job_detail_retrieval", "feed", "api"}),
    "BROAD_URL_DISCOVERY": frozenset({"broad_job_search", "url_discovery"}),
    "STRUCTURED_ACQUISITION": frozenset({"structured_acquisition", "api", "feed"}),
    "LISTING_RETRIEVAL": frozenset({"static_retrieval", "job_detail_retrieval"}),
    "BROWSER_FALLBACK": frozenset({"browser_automation", "javascript_rendering"}),
    "RECORD_VALIDATION": frozenset({"record_validation", "normalization"}),
    "RECOVERY_GAP_FILLING": frozenset({"retryable_acquisition"}),
}


class ExecutionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_requests: int | None = Field(default=None, ge=1, le=1_000_000)
    max_pages: int | None = Field(default=None, ge=1, le=1_000_000)
    max_records: int | None = Field(default=None, ge=1, le=10_000_000)
    max_duration_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_browser_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_tokens: int | None = Field(default=None, ge=1, le=100_000_000)
    max_cost_minor_units: int | None = Field(default=None, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def at_least_one_limit(self):
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("At least one execution limit is required.")
        return self


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = Field(default=1, ge=1, le=10)
    initial_backoff_seconds: int = Field(default=5, ge=0, le=3600)
    maximum_backoff_seconds: int = Field(default=300, ge=1, le=86_400)

    @model_validator(mode="after")
    def valid_backoff(self):
        if self.maximum_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("maximum_backoff_seconds must not be below the initial backoff.")
        return self


class FallbackRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")
    on_outcomes: list[Literal["FAILED", "UNAVAILABLE", "RESTRICTED", "INSUFFICIENT"]] = Field(min_length=1)

    @field_validator("on_outcomes")
    @classmethod
    def unique_outcomes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Fallback outcomes must be unique.")
        return value


class DiscoveryRoleConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_key: str
    order: int = Field(ge=1, le=100)
    enabled: bool = True
    tool_keys: list[str] = Field(min_length=1, max_length=20)
    required_capabilities: list[str] = Field(default_factory=list, max_length=30)
    source_ids: list[str] = Field(default_factory=list, max_length=1000)
    target_ids: list[str] = Field(default_factory=list, max_length=1000)
    limits: ExecutionLimits
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    fallbacks: list[FallbackRule] = Field(default_factory=list, max_length=10)
    stop_after_records: int | None = Field(default=None, ge=1, le=10_000_000)
    failure_behavior: Literal["STOP", "CONTINUE", "FALLBACK"] = "STOP"

    @model_validator(mode="after")
    def validate_role_tools(self):
        contract = ROLE_CAPABILITY_CONTRACTS.get(self.role_key)
        if contract is None:
            raise ValueError(f"Unsupported discovery role: {self.role_key}")
        if len(self.tool_keys) != len(set(self.tool_keys)):
            raise ValueError("Role tool keys must be unique.")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("Required capabilities must be unique.")
        requested = set(self.required_capabilities)
        for tool_key in self.tool_keys:
            tool = DISCOVERY_TOOL_CATALOG.get(tool_key)
            if tool is None:
                raise ValueError(f"Unknown discovery tool key: {tool_key}")
            if not tool.capabilities.intersection(contract):
                raise ValueError(f"Tool {tool_key} does not satisfy role {self.role_key}.")
            if not requested.issubset(tool.capabilities):
                missing = sorted(requested - set(tool.capabilities))
                raise ValueError(f"Tool {tool_key} lacks required capabilities: {missing}")
        fallback_keys = [fallback.tool_key for fallback in self.fallbacks]
        if len(fallback_keys) != len(set(fallback_keys)):
            raise ValueError("Fallback tool keys must be unique per role.")
        for tool_key in fallback_keys:
            tool = DISCOVERY_TOOL_CATALOG.get(tool_key)
            if tool is None or not tool.capabilities.intersection(contract):
                raise ValueError(f"Fallback tool {tool_key} is not eligible for role {self.role_key}.")
        if self.failure_behavior == "FALLBACK" and not self.fallbacks:
            raise ValueError("FALLBACK behavior requires at least one fallback rule.")
        return self


class DiscoveryStrategyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["v1"] = "v1"
    enabled: bool = True
    market_scope: list[str] = Field(default_factory=list, max_length=250)
    source_scope: list[str] = Field(default_factory=list, max_length=1000)
    target_scope: list[str] = Field(default_factory=list, max_length=1000)
    roles: list[DiscoveryRoleConfiguration] = Field(min_length=1, max_length=20)
    global_limits: ExecutionLimits
    maximum_concurrent_runs: int = Field(default=1, ge=1, le=100)
    overlap_policy: Literal["SKIP", "QUEUE", "CANCEL_PREVIOUS"] = "SKIP"

    @model_validator(mode="after")
    def unique_roles_and_order(self):
        role_keys = [role.role_key for role in self.roles]
        orders = [role.order for role in self.roles]
        if len(role_keys) != len(set(role_keys)):
            raise ValueError("Strategy role keys must be unique.")
        if len(orders) != len(set(orders)):
            raise ValueError("Strategy role order values must be unique.")
        return self


def validate_strategy_payload(payload: dict[str, Any]) -> DiscoveryStrategyPayload:
    """Return a normalized, strictly validated strategy payload."""
    return DiscoveryStrategyPayload.model_validate(payload)


def normalized_strategy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return validate_strategy_payload(payload).model_dump(mode="json")


def strategy_configuration_hash(payload: dict[str, Any]) -> str:
    normalized = normalized_strategy_payload(payload)
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def eligible_tools_for_role(role: DiscoveryRoleConfiguration) -> list[DiscoveryToolDefinition]:
    """Resolve configured tools using only the server-owned catalog and role contract."""
    requested = set(role.required_capabilities)
    contract = ROLE_CAPABILITY_CONTRACTS[role.role_key]
    return [
        DISCOVERY_TOOL_CATALOG[key]
        for key in role.tool_keys
        if DISCOVERY_TOOL_CATALOG[key].capabilities.intersection(contract)
        and requested.issubset(DISCOVERY_TOOL_CATALOG[key].capabilities)
    ]
