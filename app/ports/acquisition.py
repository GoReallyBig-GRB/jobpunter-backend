from dataclasses import dataclass
from typing import Any, Protocol

@dataclass(frozen=True)
class RawAcquisition:
    external_record_id: str | None
    source_url: str | None
    payload: dict[str, Any]
    content_hash: str

class AcquisitionConnector(Protocol):
    connector_type: str
    connector_version: str
    def acquire(self, *, configuration: dict[str, Any], context: dict[str, Any]) -> list[RawAcquisition]:
        ...
