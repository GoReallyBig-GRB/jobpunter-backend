"""Execute one durable discovery run from its immutable strategy snapshot."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.jobs.discovery_orchestration import (
    DiscoveryRoleConfiguration,
    DiscoveryStrategyPayload,
    eligible_tools_for_role,
)
from app.application.jobs.normalization import normalize_raw_record
from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
from app.application.jobs.source_governance import has_approved_permission_review
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.infrastructure.db.models.acquisition import RawSourceRecord
from app.infrastructure.db.models.discovery_orchestration import (
    DiscoveryRoleExecution,
    DiscoveryRun,
)
from app.infrastructure.db.models.discovery_target import DiscoveryTarget
from app.infrastructure.db.models.jobs import Job, JobResolution
from app.infrastructure.db.models.outbox import OutboxEvent
from app.infrastructure.db.models.source import Source, SourceConnector
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository

ACQUISITION_ROLES = {
    "SHARED_POOL_FRESHNESS", "BROAD_URL_DISCOVERY", "STRUCTURED_ACQUISITION", "LISTING_RETRIEVAL",
    "BROWSER_FALLBACK", "RECOVERY_GAP_FILLING",
}


@dataclass(frozen=True)
class ConnectorAttemptResult:
    tool_key: str
    source_id: UUID
    status: str
    target_id: UUID | None = None
    acquisition_run_id: UUID | None = None
    acquired_count: int = 0
    persisted_count: int = 0
    error_category: str | None = None


@dataclass(frozen=True)
class ConnectorCandidate:
    connector: SourceConnector
    target: DiscoveryTarget | None = None


ConnectorRunner = Callable[
    [Session, DiscoveryRun, DiscoveryRoleConfiguration, SourceConnector, DiscoveryTarget | None],
    ConnectorAttemptResult,
]


def _uuid_scope(values: list[str]) -> set[UUID]:
    return {UUID(value) for value in values}


def eligible_connectors(
    db: Session, run: DiscoveryRun, role: DiscoveryRoleConfiguration,
) -> list[ConnectorCandidate]:
    """Resolve only active, approved sources and active server-registered tool types."""
    tool_order = {tool.tool_key: index for index, tool in enumerate(eligible_tools_for_role(role))}
    allowed_tools = set(tool_order)
    fallback_tools = {fallback.tool_key for fallback in role.fallbacks}
    allowed_tools.update(fallback_tools)
    role_sources = _uuid_scope(role.source_ids)
    run_sources = _uuid_scope(run.scope.get("sources", []))
    selected_sources = role_sources or run_sources
    role_targets = _uuid_scope(role.target_ids)
    run_targets = _uuid_scope(run.scope.get("targets", []))
    selected_targets = role_targets or run_targets
    statement = (
        select(SourceConnector)
        .join(Source, Source.id == SourceConnector.source_id)
        .where(
            Source.status == "ACTIVE",
            Source.permission_review_status == "APPROVED",
            SourceConnector.status == "ACTIVE",
            SourceConnector.connector_type.in_(allowed_tools),
        )
    )
    if selected_sources:
        statement = statement.where(SourceConnector.source_id.in_(selected_sources))
    connector_rows = db.execute(
        statement.order_by(SourceConnector.source_id, SourceConnector.id)
        .add_columns(Source)
    ).all()
    connectors = [
        connector for connector, source in connector_rows
        if has_approved_permission_review(source)
    ]
    if not selected_targets:
        rows = [ConnectorCandidate(connector=row) for row in connectors]
    else:
        targets = db.scalars(select(DiscoveryTarget).where(
            DiscoveryTarget.id.in_(selected_targets),
            DiscoveryTarget.status == "ACTIVE",
            DiscoveryTarget.validation_status == "VALIDATED",
            DiscoveryTarget.source_connector_id.in_([row.id for row in connectors]),
        ).order_by(DiscoveryTarget.source_id, DiscoveryTarget.id)).all()
        registry = AcquisitionConnectorRegistry()
        connectors_by_id = {row.id: row for row in connectors}
        rows = []
        for target in targets:
            connector = connectors_by_id[target.source_connector_id]
            if target.target_kind not in registry.supported_target_kinds(
                connector.connector_type, connector.connector_version
            ):
                continue
            if connector.connector_type == "GREENHOUSE_JOB_BOARD":
                config = connector.configuration if isinstance(connector.configuration, dict) else {}
                token = config.get("board_token")
                expected = (
                    f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
                    if isinstance(token, str) and token else None
                )
                if expected is None or target.url.rstrip("/") != expected:
                    continue
            rows.append(ConnectorCandidate(connector=connector, target=target))
    rows.sort(key=lambda row: (
        str(row.connector.source_id),
        tool_order.get(row.connector.connector_type, len(tool_order)),
        str(row.target.id) if row.target else "",
        str(row.connector.id),
    ))
    return rows


def _default_connector_runner(
    db: Session, run: DiscoveryRun, role: DiscoveryRoleConfiguration, connector: SourceConnector,
    target: DiscoveryTarget | None,
) -> ConnectorAttemptResult:
    limits = role.limits.model_dump(exclude_none=True)
    context = {
        "max_pages": limits.get("max_pages") or limits.get("max_requests"),
        "max_records": limits.get("max_records"),
        "timeout_seconds": limits.get("max_duration_seconds"),
        "browser_timeout_seconds": limits.get("max_browser_seconds"),
    }
    if target is not None:
        target_context = {
            "JSON_API": {"endpoint_url": target.url},
            "RSS_ATOM": {"feed_url": target.url},
            "SCRAPY": {"start_urls": [target.url]},
            "PLAYWRIGHT": {"start_urls": [target.url]},
            "PUBLIC_HTTP": {"page_url": target.url},
            "GREENHOUSE_JOB_BOARD": {},
        }.get(connector.connector_type)
        if target_context is None:
            raise ValueError("unsupported target connector")
        context.update(target_context)
    context = {key: value for key, value in context.items() if value is not None}
    result = RegisteredAcquisitionService(SqlAlchemyAcquisitionRepository(db)).run(
        connector.source_id, connector.id,
        f"discovery-run:{run.id}:role:{role.role_key}:source:{connector.source_id}:"
        f"tool:{connector.connector_type}:target:{target.id if target else 'configured'}",
        context,
    )
    return ConnectorAttemptResult(
        tool_key=connector.connector_type, source_id=connector.source_id, status=result.status,
        target_id=target.id if target is not None else None,
        acquisition_run_id=result.run_id,
        acquired_count=result.acquired_count, persisted_count=result.persisted_count,
        error_category=result.error_category,
    )


def _fallback_outcome(status: str, acquired_count: int) -> str:
    if status == "SUCCEEDED" and acquired_count == 0:
        return "INSUFFICIENT"
    if status == "RESTRICTED":
        return "RESTRICTED"
    return status


def _bounded_role(
    role: DiscoveryRoleConfiguration,
    remaining_global_records: int | None,
    remaining_duration_seconds: int | None,
):
    limits = role.limits
    record_candidates = [
        value for value in (limits.max_records, role.stop_after_records, remaining_global_records)
        if value is not None
    ]
    updates = {}
    if record_candidates:
        updates["max_records"] = max(min(record_candidates), 1)
    if remaining_duration_seconds is not None:
        duration_candidates = [
            value for value in (limits.max_duration_seconds, remaining_duration_seconds)
            if value is not None
        ]
        updates["max_duration_seconds"] = max(min(duration_candidates), 1)
        if limits.max_browser_seconds is not None:
            updates["max_browser_seconds"] = max(
                min(limits.max_browser_seconds, remaining_duration_seconds), 1
            )
    return role if not updates else role.model_copy(
        update={"limits": limits.model_copy(update=updates)}
    )


def _remaining_seconds(deadline: datetime | None) -> int | None:
    if deadline is None:
        return None
    return max(int((deadline - datetime.now(UTC)).total_seconds()), 0)


def _has_valid_record_identity(
    db: Session, record: RawSourceRecord, normalized: dict,
) -> bool:
    if normalized.get("canonical_title"):
        return True
    if not record.external_record_id:
        return False
    # Match canonicalization's only title-less exception: a stable external ID
    # may identify a canonical job already resolved from this same source.
    existing = db.scalar(
        select(JobResolution.id)
        .join(RawSourceRecord, RawSourceRecord.id == JobResolution.raw_source_record_id)
        .join(Job, Job.id == JobResolution.job_id)
        .where(
            RawSourceRecord.source_id == record.source_id,
            RawSourceRecord.external_record_id == record.external_record_id,
            RawSourceRecord.id != record.id,
            Job.identity_status == "CANONICAL",
            JobResolution.job_id.is_not(None),
        )
        .limit(1)
    )
    return existing is not None


def _execute_record_validation(
    db: Session, run: DiscoveryRun, execution: DiscoveryRoleExecution,
) -> str:
    prior = db.scalars(select(DiscoveryRoleExecution).where(
        DiscoveryRoleExecution.run_id == run.id,
        DiscoveryRoleExecution.attempt_number == execution.attempt_number,
        DiscoveryRoleExecution.role_order < execution.role_order,
    )).all()
    acquisition_run_ids = {
        UUID(item["acquisition_run_id"])
        for row in prior
        for item in (row.fallback_decision or {}).get("attempts", [])
        if item.get("acquisition_run_id")
    }
    execution.selected_tool_key = "INTERNAL_RECORD_VALIDATOR"
    execution.budget_usage = {"internal_validation_passes": 1}
    if not acquisition_run_ids:
        execution.outcome = "SKIPPED"
        execution.reason_code = "NO_ACQUISITION_RECORDS"
        execution.record_counts = {
            "examined": 0, "valid": 0, "invalid": 0,
            "canonicalization_handoff_missing": 0,
        }
        execution.completed_at = datetime.now(UTC)
        db.commit()
        return execution.outcome
    records = db.scalars(select(RawSourceRecord).where(
        RawSourceRecord.acquisition_run_id.in_(acquisition_run_ids)
    )).all()
    if not records:
        execution.outcome = "SKIPPED"
        execution.reason_code = "NO_VALIDATABLE_RECORDS"
        execution.record_counts = {
            "examined": 0, "valid": 0, "invalid": 0,
            "canonicalization_handoff_missing": 0,
        }
        execution.completed_at = datetime.now(UTC)
        db.commit()
        return execution.outcome
    valid = 0
    for record in records:
        try:
            normalized = normalize_raw_record(record.raw_content)
        except (AttributeError, TypeError, ValueError, KeyError):
            continue
        if _has_valid_record_identity(db, record, normalized):
            valid += 1
    raw_ids = [record.id for record in records]
    handed_off = set(db.scalars(select(OutboxEvent.aggregate_id).where(
        OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
        OutboxEvent.aggregate_type == "RawSourceRecord",
        OutboxEvent.aggregate_id.in_(raw_ids),
    )).all()) if raw_ids else set()
    invalid = len(records) - valid
    missing_handoff = len(set(raw_ids) - handed_off)
    execution.record_counts = {
        "examined": len(records), "valid": valid, "invalid": invalid,
        "canonicalization_handoff_missing": missing_handoff,
    }
    if invalid == 0 and missing_handoff == 0:
        execution.outcome = "SUCCEEDED"
    elif valid > 0:
        execution.outcome = "PARTIAL"
        execution.reason_code = "RECORD_VALIDATION_INCOMPLETE"
    else:
        execution.outcome = "FAILED"
        execution.reason_code = "NO_VALID_ACQUIRED_RECORDS"
    execution.completed_at = datetime.now(UTC)
    db.commit()
    return execution.outcome


def execute_discovery_run(
    db: Session, run_id: UUID, *, attempt_number: int,
    connector_runner: ConnectorRunner = _default_connector_runner,
) -> str:
    """Execute enabled roles in order and persist truthful role/run outcomes."""
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise ValueError("discovery run does not exist")
    if run.status == "CANCELLED":
        return run.status
    strategy = DiscoveryStrategyPayload.model_validate(run.configuration_snapshot["strategy"])
    run.status = "RUNNING"
    run.started_at = run.started_at or datetime.now(UTC)
    run.revision += 1
    db.commit()

    total_acquired = 0
    total_persisted = 0
    connector_attempts = 0
    role_outcomes: list[str] = []
    global_records = strategy.global_limits.max_records
    global_requests = strategy.global_limits.max_requests
    deadline = (
        run.started_at + timedelta(seconds=strategy.global_limits.max_duration_seconds)
        if strategy.global_limits.max_duration_seconds is not None else None
    )

    for role in sorted((item for item in strategy.roles if item.enabled), key=lambda item: item.order):
        db.refresh(run)
        if run.status == "CANCELLED":
            return run.status
        role_started_at = datetime.now(UTC)
        role_deadline = (
            role_started_at + timedelta(seconds=role.limits.max_duration_seconds)
            if role.limits.max_duration_seconds is not None else None
        )
        effective_deadline = min(
            (item for item in (deadline, role_deadline) if item is not None),
            default=None,
        )
        execution = DiscoveryRoleExecution(
            run_id=run.id, strategy_version_id=run.strategy_version_id,
            role_key=role.role_key, role_order=role.order, attempt_number=attempt_number,
            eligibility={"tool_keys": [tool.tool_key for tool in eligible_tools_for_role(role)]},
            outcome="RUNNING", input_scope={
                "sources": role.source_ids or run.scope.get("sources", []),
                "targets": role.target_ids or run.scope.get("targets", []),
            },
            record_counts={}, budget_usage={}, fallback_decision={},
            correlation_id=run.correlation_id, started_at=role_started_at,
        )
        db.add(execution)
        db.commit()

        if role.role_key == "RECORD_VALIDATION":
            role_outcomes.append(_execute_record_validation(db, run, execution))
            if execution.outcome == "FAILED" and role.failure_behavior == "STOP":
                break
            continue
        if role.role_key not in ACQUISITION_ROLES:
            execution.outcome = "BLOCKED"
            execution.reason_code = "ROLE_HANDLER_UNAVAILABLE"
            execution.completed_at = datetime.now(UTC)
            role_outcomes.append(execution.outcome)
            db.commit()
            if role.failure_behavior == "STOP":
                break
            continue

        connectors = eligible_connectors(db, run, role)
        by_source: dict[UUID, list[ConnectorCandidate]] = {}
        for candidate in connectors:
            by_source.setdefault(candidate.connector.source_id, []).append(candidate)
        results: list[ConnectorAttemptResult] = []
        final_statuses: list[str] = []
        fallback_events: list[dict] = []
        attempted_candidates: set[tuple[UUID, UUID | None]] = set()
        role_request_limit = role.limits.max_requests
        role_record_candidates = [
            value for value in (role.limits.max_records, role.stop_after_records)
            if value is not None
        ]
        role_record_limit = min(role_record_candidates) if role_record_candidates else None
        for source_id, candidates in by_source.items():
            if effective_deadline is not None and datetime.now(UTC) >= effective_deadline:
                execution.reason_code = (
                    "GLOBAL_DURATION_BUDGET_EXHAUSTED"
                    if deadline is not None and deadline <= effective_deadline
                    else "ROLE_DURATION_BUDGET_EXHAUSTED"
                )
                break
            primary_keys = set(role.tool_keys)
            primaries = [item for item in candidates if item.connector.connector_type in primary_keys]
            if not primaries:
                continue
            for primary in primaries:
                if effective_deadline is not None and datetime.now(UTC) >= effective_deadline:
                    execution.reason_code = (
                        "GLOBAL_DURATION_BUDGET_EXHAUSTED"
                        if deadline is not None and deadline <= effective_deadline
                        else "ROLE_DURATION_BUDGET_EXHAUSTED"
                    )
                    break
                primary_key = (
                    primary.connector.id, primary.target.id if primary.target is not None else None
                )
                if primary_key in attempted_candidates:
                    continue
                if global_requests is not None and connector_attempts >= global_requests:
                    execution.reason_code = "GLOBAL_REQUEST_BUDGET_EXHAUSTED"
                    break
                if role_request_limit is not None and len(results) >= role_request_limit:
                    execution.reason_code = "ROLE_REQUEST_BUDGET_EXHAUSTED"
                    break
                remaining = None if global_records is None else global_records - (
                    total_acquired + sum(item.acquired_count for item in results)
                )
                result = connector_runner(
                    db, run, _bounded_role(
                        role, remaining, _remaining_seconds(effective_deadline)
                    ), primary.connector, primary.target
                )
                connector_attempts += 1
                results.append(result)
                attempted_candidates.add(primary_key)
                final_result = result
                outcome = _fallback_outcome(result.status, result.acquired_count)
                rule = next((item for item in role.fallbacks if outcome in item.on_outcomes), None)
                fallback = next((
                    item for item in candidates
                    if rule is not None
                    and item.connector.connector_type == rule.tool_key
                    and item.connector.id != primary.connector.id
                    and (
                        item.connector.id, item.target.id if item.target is not None else None
                    ) not in attempted_candidates
                ), None)
                if (
                    fallback is not None
                    and (global_requests is None or connector_attempts < global_requests)
                    and (role_request_limit is None or len(results) < role_request_limit)
                    and (
                        global_records is None
                        or total_acquired + sum(item.acquired_count for item in results)
                        < global_records
                    )
                    and (
                        effective_deadline is None or datetime.now(UTC) < effective_deadline
                    )
                ):
                    remaining = None if global_records is None else global_records - (
                        total_acquired + sum(item.acquired_count for item in results)
                    )
                    fallback_result = connector_runner(
                        db, run, _bounded_role(
                            role, remaining, _remaining_seconds(effective_deadline)
                        ),
                        fallback.connector, fallback.target,
                    )
                    connector_attempts += 1
                    results.append(fallback_result)
                    final_result = fallback_result
                    attempted_candidates.add((
                        fallback.connector.id,
                        fallback.target.id if fallback.target is not None else None,
                    ))
                    fallback_events.append({
                        "source_id": str(source_id), "from": primary.connector.connector_type,
                        "to": fallback.connector.connector_type, "outcome": outcome,
                    })
                final_statuses.append(final_result.status)
                if global_records is not None and (
                    total_acquired + sum(item.acquired_count for item in results) >= global_records
                ):
                    break
            current_role_records = sum(item.acquired_count for item in results)
            if global_records is not None and total_acquired + current_role_records >= global_records:
                execution.reason_code = "GLOBAL_RECORD_BUDGET_EXHAUSTED"
                break
            if role_record_limit is not None and current_role_records >= role_record_limit:
                execution.reason_code = "ROLE_RECORD_BUDGET_EXHAUSTED"
                break

        role_acquired = sum(item.acquired_count for item in results)
        role_persisted = sum(item.persisted_count for item in results)
        total_acquired += role_acquired
        total_persisted += role_persisted
        statuses = set(final_statuses)
        if not results:
            execution.outcome = "BLOCKED"
            execution.reason_code = execution.reason_code or (
                "NO_ELIGIBLE_TARGET"
                if role.target_ids or run.scope.get("targets")
                else "NO_ELIGIBLE_CONNECTOR"
            )
        elif statuses <= {"SUCCEEDED"}:
            execution.outcome = "SUCCEEDED"
        elif "SUCCEEDED" in statuses or "PARTIAL" in statuses or role_acquired > 0:
            execution.outcome = "PARTIAL"
        else:
            execution.outcome = "FAILED"
            execution.reason_code = next((item.error_category for item in results if item.error_category), "ACQUISITION_FAILED")
        execution.selected_tool_key = results[0].tool_key if len({item.tool_key for item in results}) == 1 else None
        execution.record_counts = {"acquired": role_acquired, "persisted": role_persisted}
        execution.budget_usage = {
            "connector_attempts": len(results),
            "elapsed_seconds": max(
                (datetime.now(UTC) - role_started_at).total_seconds(), 0
            ),
        }
        execution.fallback_decision = {
            "events": fallback_events,
            "attempts": [{
                "source_id": str(item.source_id), "target_id": str(item.target_id) if item.target_id else None,
                "acquisition_run_id": (
                    str(item.acquisition_run_id) if item.acquisition_run_id else None
                ),
                "tool_key": item.tool_key, "status": item.status,
                "acquired_count": item.acquired_count,
                "persisted_count": item.persisted_count,
                "error_category": item.error_category,
            } for item in results],
        }
        execution.completed_at = datetime.now(UTC)
        role_outcomes.append(execution.outcome)
        db.commit()
        if execution.outcome in {"FAILED", "BLOCKED"} and role.failure_behavior == "STOP":
            break

    run.operation_counts = {"connector_attempts": connector_attempts}
    run.record_counts = {"acquired": total_acquired, "persisted": total_persisted}
    run.budget_outcome = {
        "global_request_limit": global_requests, "global_record_limit": global_records,
        "request_limit_reached": global_requests is not None and connector_attempts >= global_requests,
        "record_limit_reached": global_records is not None and total_acquired >= global_records,
        "duration_limit_reached": deadline is not None and datetime.now(UTC) >= deadline,
    }
    if not role_outcomes or set(role_outcomes) <= {"BLOCKED", "SKIPPED"}:
        run.status = "BLOCKED"
    elif any(item in {"FAILED", "BLOCKED", "PARTIAL"} for item in role_outcomes):
        run.status = "PARTIAL" if total_acquired > 0 else "FAILED"
    else:
        run.status = "SUCCEEDED"
    run.completed_at = datetime.now(UTC)
    run.lease_expires_at = None
    run.revision += 1
    db.commit()
    return run.status
