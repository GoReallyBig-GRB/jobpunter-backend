from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class RawAcquisition:
    source_id: UUID
    external_record_id: str | None
    source_url: str | None
    raw_content: dict[str, Any]
    content_hash: str
    observed_at: datetime


class AcquisitionOutcome(StrEnum):
    """Truthful terminal outcome of one connector acquisition attempt."""

    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    RESTRICTED = "RESTRICTED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AcquisitionFailure:
    """Sanitized failure metadata; never store arbitrary provider response bodies."""

    category: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.category, str):
            raise ValueError("acquisition failure category must be a string")
        category = self.category.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,63}", category):
            raise ValueError("acquisition failure category must be a safe 1 to 64 character code")
        object.__setattr__(self, "category", category)


@dataclass(frozen=True)
class AcquisitionBatchResult:
    """Connector result that distinguishes empty success from incomplete or failed work.

    PARTIAL means at least one record was acquired and a material failure also occurred.
    A terminal non-success result carries no records; if some records were retained before
    a restriction or outage, report PARTIAL and classify the failure in error_category.
    """

    status: AcquisitionOutcome
    records: tuple[RawAcquisition, ...] = ()
    error_category: str | None = None
    failures: tuple[AcquisitionFailure, ...] = ()
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        try:
            normalized_status = AcquisitionOutcome(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported acquisition outcome status") from exc
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "failures", tuple(self.failures))

        if self.error_category is not None:
            category = self.error_category.strip().upper()
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,63}", category):
                raise ValueError("error_category must be a safe 1 to 64 character code")
            object.__setattr__(self, "error_category", category)

        if self.retry_after_seconds is not None and (
            not isinstance(self.retry_after_seconds, int)
            or isinstance(self.retry_after_seconds, bool)
            or self.retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be a non-negative integer")

        if normalized_status == AcquisitionOutcome.SUCCEEDED:
            if self.failures or self.error_category:
                raise ValueError("successful acquisition cannot contain failure metadata")
        elif normalized_status == AcquisitionOutcome.PARTIAL:
            if not self.records:
                raise ValueError("partial acquisition must contain at least one acquired record")
            if not self.failures and not self.error_category:
                raise ValueError("partial acquisition must identify its failure")
        else:
            if self.records:
                raise ValueError("terminal non-success acquisition cannot contain records")
            if not self.error_category and not self.failures:
                raise ValueError("non-success acquisition must identify its failure")


class AcquisitionConnector(Protocol):
    connector_type: str
    connector_version: str

    def acquire(
        self, *, source_id: UUID, context: dict[str, Any]
    ) -> list[RawAcquisition] | AcquisitionBatchResult:
        """Return a typed outcome; list results remain accepted for legacy success-only adapters."""
        ...
