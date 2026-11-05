"""Execute one source/connector/target scope and record its truthful outcome.

Shared by candidate discovery missions and scheduled source refreshes so both apply exactly the
same policy: administrative tool pause, source run-rate limit, target eligibility, connector
capability, permission review, then the registered acquisition service.
"""
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select

from app.application.jobs.source_governance import assert_automated_acquisition_permitted
from app.application.jobs.source_limits import effective_config_snapshot, run_budget_reached, source_run_limit_reached
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.infrastructure.db.models.discovery_tool import DiscoveryToolControl
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository

logger = logging.getLogger("jobpunter.source_scope_execution")


def execute_source_scope(
    db, *, source, connector, target, source_run, run_key: str, service_factory: Callable,
) -> None:
    """Mutate and commit `source_run` (already persisted as RUNNING) with the scope's outcome.

    `service_factory(repository, registry)` builds the acquisition service; callers pass their own
    so test doubles and per-caller wiring keep working.
    """
    source_run_id = source_run.id
    source_id = source.id
    connector_type_label = connector.connector_type
    # An administratively disabled tool never starts new work (E3.4); this is a
    # recorded skip, not a failure and not a successful empty acquisition.
    tool_key = str(connector.connector_type).strip().upper()
    schedule = None
    if getattr(source_run, "schedule_id", None) is not None:
        from app.infrastructure.db.models.discovery_schedule import SourceRefreshSchedule

        schedule = db.get(SourceRefreshSchedule, source_run.schedule_id)
    source_run.config_snapshot = effective_config_snapshot(
        db, source_id=source.id, tool_key=tool_key, schedule=schedule,
    )
    db.commit()  # keep the explanation even if the acquisition below raises and rolls back
    if db.scalar(
        select(DiscoveryToolControl.status)
        .where(DiscoveryToolControl.tool_key == tool_key)
        .execution_options(populate_existing=True)
    ) == "DISABLED":
        source_run.status = "UNAVAILABLE"
        source_run.error_code = "DISCOVERY_TOOL_DISABLED"
        source_run.error_details = {
            **(source_run.error_details or {}),
            "scope_outcome": "SKIPPED",
            "tool_key": tool_key,
        }
        source_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    # A source that has used its administrative run-rate allowance is paced, not failed.
    limit_state = source_run_limit_reached(db, source.id)
    if limit_state is not None:
        source_run.status = "UNAVAILABLE"
        source_run.error_code = "SOURCE_RATE_LIMIT_REACHED"
        source_run.error_details = {
            **(source_run.error_details or {}),
            "scope_outcome": "SKIPPED",
            "max_runs": limit_state["max_runs"],
            "window_minutes": limit_state["window_minutes"],
        }
        source_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    # A tool or global run budget that is spent defers new work (E4.6): a recorded skip, never a failure.
    budget_state = run_budget_reached(db, tool_key)
    if budget_state is not None:
        source_run.status = "UNAVAILABLE"
        source_run.error_code = "RUN_BUDGET_REACHED"
        source_run.error_details = {**(source_run.error_details or {}), "scope_outcome": "SKIPPED", **budget_state}
        source_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    # No target or a non-active/blocked target is a recorded skip, never
    # a successful empty acquisition and never a network request.
    if target is None:
        source_run.status = "UNAVAILABLE"
        source_run.error_code = "NO_CONFIGURED_DISCOVERY_TARGET"
        source_run.error_details = {"scope_outcome": "SKIPPED"}
        source_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return
    if target.status != "ACTIVE" or target.validation_status != "VALIDATED":
        blocked = target.status == "RETIRED" or target.validation_status == "BLOCKED"
        source_run.status = "RESTRICTED" if blocked else "UNAVAILABLE"
        source_run.error_code = (
            "DISCOVERY_TARGET_BLOCKED" if blocked else "DISCOVERY_TARGET_NOT_ACTIVE_OR_VALIDATED"
        )
        source_run.error_details = {
            **(source_run.error_details or {}),
            "scope_outcome": "SKIPPED",
            "target_status": target.status,
            "validation_status": target.validation_status,
        }
        source_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    # Target kinds are connector contracts, not hints. Never fall back to a
    # connector's broader configured URL list when a target cannot be mapped.
    connector_type = str(connector.connector_type).strip().upper()
    target_kind = str(target.target_kind).strip().upper()
    supported_target_kinds = set(AcquisitionConnectorRegistry().supported_target_kinds(
        connector.connector_type, connector.connector_version
    ))
    if target_kind not in supported_target_kinds:
        source_run.status = "UNAVAILABLE"
        source_run.error_code = "UNSUPPORTED_TARGET_KIND_FOR_CONNECTOR"
        source_run.error_details = {
            **(source_run.error_details or {}),
            "connector_type": connector_type,
            "scope_outcome": "SKIPPED",
        }
        source_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    # The persisted, validated target narrows each connector run to one URL.
    # It never grants capabilities or overrides the connector's security policy.
    context_override = {
        "JSON_API": {"endpoint_url": target.url},
        "RSS_ATOM": {"feed_url": target.url},
        "SCRAPY": {"start_urls": [target.url]},
        "PLAYWRIGHT": {"start_urls": [target.url]},
        "PUBLIC_HTTP": {"page_url": target.url},
        "JOBSPY": {},
        # Greenhouse is board-token based. Permit only the canonical jobs URL
        # derived from the persisted board token, never an arbitrary target URL.
        "GREENHOUSE_JOB_BOARD": {},
    }.get(connector_type)
    if connector_type == "GREENHOUSE_JOB_BOARD":
        connector_config = connector.configuration if isinstance(connector.configuration, dict) else {}
        board_token = connector_config.get("board_token")
        expected_target_url = (
            f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
            if isinstance(board_token, str) and board_token
            else None
        )
        if expected_target_url is None or target.url.rstrip("/") != expected_target_url:
            source_run.status = "UNAVAILABLE"
            source_run.error_code = "TARGET_URL_NOT_BOUND_TO_CONNECTOR_CONFIGURATION"
            source_run.error_details = {
                **(source_run.error_details or {}),
                "scope_outcome": "SKIPPED",
            }
            source_run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
    if context_override is None:
        source_run.status = "UNAVAILABLE"
        source_run.error_code = "UNSUPPORTED_TARGET_CONNECTOR"
        source_run.error_details = {
            **(source_run.error_details or {}),
            "connector_type": connector_type,
            "scope_outcome": "SKIPPED",
        }
        source_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    # Record permission restrictions as a truthful terminal source outcome.
    # The registered acquisition service also enforces this invariant, but
    # the mission layer must not misclassify a policy block as a connector failure.
    try:
        assert_automated_acquisition_permitted(source)
    except ValueError:
        restricted_at = datetime.now(timezone.utc)
        source.operational_state = "RESTRICTED"
        source.last_error_category = "PERMISSION_REVIEW_REQUIRED"
        source.updated_at = restricted_at
        source_run.status = "RESTRICTED"
        source_run.error_code = "PERMISSION_REVIEW_REQUIRED"
        source_run.error_details = {"reason_code": "SOURCE_PERMISSION_REVIEW_REQUIRED"}
        source_run.completed_at = restricted_at
        db.commit()
        return

    # Candidate-owned mission snapshots are filters, not connector configuration.
    # Ignore legacy snapshots containing acquisition_context so they cannot override
    # admin-published secrets, endpoints, or connector policy. The target URL above is
    # the only per-run endpoint override and is independently revalidated by the adapter.
    try:
        result = service_factory(
            SqlAlchemyAcquisitionRepository(db), AcquisitionConnectorRegistry()
        ).run(source.id, connector.id, run_key, context_override)
        source_run.acquisition_run_id = result.run_id
        source_run.acquired_count = result.acquired_count
        source_run.persisted_count = result.persisted_count
        outcome_status = {
            "SUCCEEDED": "SUCCEEDED_EMPTY" if result.acquired_count == 0 else "SUCCEEDED",
            "PARTIAL": "PARTIAL",
            "RESTRICTED": "RESTRICTED",
            "UNAVAILABLE": "UNAVAILABLE",
            "FAILED": "FAILED",
        }.get(result.status, "FAILED")
        source_run.status = outcome_status
        if result.status != "SUCCEEDED":
            source_run.error_code = result.error_category or (
                "UNRECOGNIZED_ACQUISITION_OUTCOME"
                if result.status not in {"PARTIAL", "RESTRICTED", "UNAVAILABLE", "FAILED"}
                else result.status
            )
            source_run.error_details = {
                "failed_item_count": result.failed_item_count,
                "retry_after_seconds": result.retry_after_seconds,
            }
        source_run.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        failed_row = db.get(type(source_run), source_run_id)
        if failed_row is not None:
            failed_row.status = "FAILED"
            failed_row.error_code = type(exc).__name__
            failed_row.error_details = {"error_type": type(exc).__name__}
            failed_row.completed_at = datetime.now(timezone.utc)
            db.commit()
        logger.warning(
            "discovery source failed run=%s source=%s connector=%s error=%s",
            source_run_id, source_id, connector_type_label, type(exc).__name__,
        )

