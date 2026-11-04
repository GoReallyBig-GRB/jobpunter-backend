import pytest
from pydantic import ValidationError
from app.application.jobs.discovery_orchestration import (
    DiscoveryRoleConfiguration,
    eligible_tools_for_role,
    strategy_configuration_hash,
    validate_strategy_payload,
)
from app.application.jobs.discovery_run_execution import _bounded_role


def _payload():
    return {
        "schema_version": "v1",
        "enabled": True,
        "roles": [
            {
                "role_key": "STRUCTURED_ACQUISITION",
                "order": 1,
                "tool_keys": ["GREENHOUSE_JOB_BOARD", "RSS_ATOM"],
                "required_capabilities": ["structured_acquisition"],
                "limits": {"max_requests": 25, "max_records": 500},
                "retry": {
                    "max_attempts": 2,
                    "initial_backoff_seconds": 5,
                    "maximum_backoff_seconds": 60,
                },
                "failure_behavior": "FALLBACK",
                "fallbacks": [{"tool_key": "JSON_API", "on_outcomes": ["UNAVAILABLE"]}],
            },
            {
                "role_key": "RECORD_VALIDATION",
                "order": 2,
                "tool_keys": ["INTERNAL_RECORD_VALIDATOR"],
                "limits": {"max_records": 500, "max_duration_seconds": 300},
            },
        ],
        "global_limits": {"max_requests": 50, "max_records": 1000, "max_duration_seconds": 900},
        "maximum_concurrent_runs": 2,
        "overlap_policy": "SKIP",
    }


def test_strategy_schema_accepts_registered_capable_tools_and_stable_hash():
    first = validate_strategy_payload(_payload())
    reordered = {key: _payload()[key] for key in reversed(_payload())}
    assert strategy_configuration_hash(_payload()) == strategy_configuration_hash(reordered)
    assert [tool.tool_key for tool in eligible_tools_for_role(first.roles[0])] == [
        "GREENHOUSE_JOB_BOARD",
        "RSS_ATOM",
    ]


def test_strategy_schema_rejects_unknown_fields_and_executable_content():
    payload = _payload()
    payload["python"] = "import os"
    with pytest.raises(ValidationError):
        validate_strategy_payload(payload)


def test_strategy_schema_rejects_tool_without_role_capability():
    payload = _payload()
    payload["roles"][0]["tool_keys"] = ["PLAYWRIGHT"]
    with pytest.raises(ValidationError, match="does not satisfy role"):
        validate_strategy_payload(payload)


def test_strategy_schema_requires_fallback_rule_for_fallback_behavior():
    payload = _payload()
    payload["roles"][0]["fallbacks"] = []
    with pytest.raises(ValidationError, match="requires at least one fallback"):
        validate_strategy_payload(payload)


def test_strategy_schema_rejects_unbounded_roles_and_global_execution():
    payload = _payload()
    payload["roles"][0]["limits"] = {}
    with pytest.raises(ValidationError, match="At least one execution limit"):
        validate_strategy_payload(payload)
    with pytest.raises(ValidationError, match="At least one execution limit"):
        DiscoveryRoleConfiguration.model_validate(
            {
                "role_key": "RECORD_VALIDATION",
                "order": 1,
                "tool_keys": ["INTERNAL_RECORD_VALIDATOR"],
                "limits": {},
            }
        )


def test_strategy_schema_rejects_non_uuid_resource_scopes():
    payload = _payload()
    payload["source_scope"] = ["not-a-source-id"]
    with pytest.raises(ValidationError, match="must be UUIDs"):
        validate_strategy_payload(payload)


def test_strategy_schema_rejects_unmetered_role_token_and_cost_limits():
    for limit in ({"max_tokens": 100}, {"max_cost_minor_units": 10}):
        payload = _payload()
        payload["roles"][0]["limits"] = limit
        with pytest.raises(ValidationError, match="require metered discovery tools"):
            validate_strategy_payload(payload)


def test_strategy_schema_rejects_unmetered_aggregate_limits():
    for limit in (
        {"max_pages": 10},
        {"max_browser_seconds": 10},
        {"max_tokens": 100},
        {"max_cost_minor_units": 10},
    ):
        payload = _payload()
        payload["global_limits"] = limit
        with pytest.raises(ValidationError, match="require aggregate metering"):
            validate_strategy_payload(payload)


def test_role_execution_limits_are_bounded_by_remaining_global_budgets():
    role = DiscoveryRoleConfiguration.model_validate(
        {
            "role_key": "BROWSER_FALLBACK",
            "order": 1,
            "tool_keys": ["PLAYWRIGHT"],
            "limits": {
                "max_records": 100,
                "max_duration_seconds": 60,
                "max_browser_seconds": 45,
            },
        }
    )

    bounded = _bounded_role(role, remaining_global_records=25, remaining_duration_seconds=20)

    assert bounded.limits.max_records == 25
    assert bounded.limits.max_duration_seconds == 20
    assert bounded.limits.max_browser_seconds == 20
