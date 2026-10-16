from dataclasses import dataclass
from uuid import UUID

from app.ports.acquisition import (
    AcquisitionBatchResult,
    AcquisitionConnector,
    AcquisitionOutcome,
)
from app.ports.repositories.acquisition import AcquisitionRepository


@dataclass(frozen=True)
class AcquisitionResult:
    run_id: UUID
    status: str
    acquired_count: int
    persisted_count: int
    observed_count: int = 0
    new_count: int = 0
    changed_count: int = 0
    unchanged_count: int = 0
    error_category: str | None = None
    failed_item_count: int = 0
    retry_after_seconds: int | None = None

    @property
    def duplicate_count(self) -> int:
        """Records fetched from the source that were not newly persisted due to deduplication."""
        return max(self.acquired_count - self.persisted_count, 0)


class AcquisitionService:
    def __init__(self, repository: AcquisitionRepository, connector: AcquisitionConnector):
        self.repository = repository
        self.connector = connector

    @staticmethod
    def _result(run) -> AcquisitionResult:
        metrics = run.metrics or {}
        return AcquisitionResult(
            run_id=run.id,
            status=run.status,
            acquired_count=int(metrics.get("acquired_count", 0)),
            persisted_count=int(metrics.get("persisted_count", 0)),
            observed_count=int(metrics.get("observed_count", 0)),
            new_count=int(metrics.get("new_count", 0)),
            changed_count=int(metrics.get("changed_count", 0)),
            unchanged_count=int(metrics.get("unchanged_count", 0)),
            error_category=metrics.get("error_category"),
            failed_item_count=int(metrics.get("failed_item_count", 0)),
            retry_after_seconds=metrics.get("retry_after_seconds"),
        )

    @staticmethod
    def _normalize_connector_result(value) -> AcquisitionBatchResult:
        if isinstance(value, AcquisitionBatchResult):
            return value
        # Compatibility for existing success-only connectors. An empty list is a valid
        # successful no-results response, not an implicit failure.
        if isinstance(value, (list, tuple)):
            return AcquisitionBatchResult(
                status=AcquisitionOutcome.SUCCEEDED,
                records=tuple(value),
            )
        raise TypeError("connector must return AcquisitionBatchResult or a list of RawAcquisition")

    def run(
        self, source_id: UUID, connector_id: UUID, run_key: str, context: dict
    ) -> AcquisitionResult:
        recover_stale_runs = getattr(self.repository, "recover_stale_runs", None)
        if callable(recover_stale_runs):
            recover_stale_runs()
        existing = self.repository.get_run(source_id, run_key)
        if existing:
            return self._result(existing)

        connector_row = self.repository.get_connector(connector_id)
        if not connector_row or connector_row.source_id != source_id:
            raise ValueError("connector does not belong to source")

        run, created = self.repository.create_run(source_id, connector_id, run_key)
        if not created:
            # A concurrent request claimed this idempotency key first.
            return self._result(run)

        try:
            batch = self._normalize_connector_result(
                self.connector.acquire(source_id=source_id, context=context)
            )
        except Exception as exc:
            self.repository.fail_run(
                run.id,
                error_summary=f"connector_error:{type(exc).__name__}",
            )
            raise

        error_category = batch.error_category
        if error_category is None and batch.failures:
            error_category = batch.failures[0].category
        failure_categories = [failure.category for failure in batch.failures]

        # A terminal no-record outcome is still a completed, observable acquisition attempt.
        # It must not be rewritten as success or retried as though a handler were missing.
        if batch.status in {
            AcquisitionOutcome.RESTRICTED,
            AcquisitionOutcome.UNAVAILABLE,
            AcquisitionOutcome.FAILED,
        }:
            self.repository.complete_run(
                run.id,
                acquired_count=0,
                persisted_count=0,
                status=batch.status.value,
                error_category=error_category,
                failure_count=len(batch.failures),
                failure_categories=failure_categories,
                retry_after_seconds=batch.retry_after_seconds,
            )
            completed = self.repository.get_run(source_id, run_key)
            if completed is None:
                raise RuntimeError("Completed acquisition run could not be retrieved")
            return self._result(completed)

        persisted_count = 0
        try:
            record_observation = getattr(self.repository, "record_observation", None)
            for item in batch.records:
                persisted_new = self.repository.persist_raw_if_new(run.id, item)
                if persisted_new:
                    persisted_count += 1
                if callable(record_observation):
                    record_observation(run.id, item, persisted_new=persisted_new)
            self.repository.complete_run(
                run.id,
                acquired_count=len(batch.records),
                persisted_count=persisted_count,
                status=batch.status.value,
                error_category=error_category,
                failure_count=len(batch.failures),
                failure_categories=failure_categories,
                retry_after_seconds=batch.retry_after_seconds,
            )
        except Exception as exc:
            self.repository.fail_run(
                run.id,
                error_summary=f"persistence_error:{type(exc).__name__}",
                acquired_count=len(batch.records),
                # Raw rows and outbox events share one transaction; fail_run rolls it back.
                persisted_count=0,
            )
            raise

        completed = self.repository.get_run(source_id, run_key)
        if completed is None:
            raise RuntimeError("Completed acquisition run could not be retrieved")
        return self._result(completed)
