from dataclasses import dataclass
from datetime import datetime
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


class AcquisitionConnector(Protocol):
    connector_type: str
    connector_version: str

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> list[RawAcquisition]:
        ...
