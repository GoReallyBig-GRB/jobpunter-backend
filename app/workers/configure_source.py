"""Idempotently register one source and its connector from an operator-owned JSON file.

Credentials must not be placed in this file. Supply secrets through environment-backed
configuration or a dedicated secret store when a connector needs them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.infrastructure.db.models import Source, SourceConnector
from app.infrastructure.db.session import SessionLocal


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def configure_source(db: Session, config: dict[str, Any]) -> dict[str, str]:
    """Create or reuse a source and connector; reject ambiguous or unsupported config."""
    source_cfg = _object(config.get("source"), "source")
    connector_cfg = _object(config.get("connector"), "connector")

    name = str(source_cfg.get("name", "")).strip()
    source_type = str(source_cfg.get("source_type", "")).strip().upper()
    base_url = source_cfg.get("base_url")
    if not name or len(name) > 255:
        raise ValueError("source.name must contain 1 to 255 characters")
    if not source_type or len(source_type) > 64:
        raise ValueError("source.source_type must contain 1 to 64 characters")
    if base_url is not None and not isinstance(base_url, str):
        raise ValueError("source.base_url must be a string or null")

    connector_type = str(connector_cfg.get("connector_type", "")).strip().upper()
    connector_version = str(connector_cfg.get("connector_version", "")).strip()
    if not connector_type or len(connector_type) > 64:
        raise ValueError("connector.connector_type must contain 1 to 64 characters")
    if not connector_version or len(connector_version) > 64:
        raise ValueError("connector.connector_version must contain 1 to 64 characters")
    if (connector_type, connector_version) not in AcquisitionConnectorRegistry().supported_connectors:
        raise ValueError(f"unsupported connector: {connector_type}@{connector_version}")

    source_configuration = _object(source_cfg.get("configuration", {}), "source.configuration")
    connector_configuration = _object(connector_cfg.get("configuration", {}), "connector.configuration")
    capabilities = _object(connector_cfg.get("capabilities", {}), "connector.capabilities")
    limits = _object(connector_cfg.get("limits", {}), "connector.limits")
    source_status = str(source_cfg.get("status", "ACTIVE")).strip().upper()
    connector_status = str(connector_cfg.get("status", "ACTIVE")).strip().upper()
    if source_status not in {"ACTIVE", "PAUSED", "DISABLED"}:
        raise ValueError("source.status must be ACTIVE, PAUSED, or DISABLED")
    if connector_status not in {"ACTIVE", "PAUSED", "DISABLED"}:
        raise ValueError("connector.status must be ACTIVE, PAUSED, or DISABLED")

    matches = list(db.scalars(select(Source).where(
        Source.name == name,
        Source.source_type == source_type,
        Source.base_url == base_url,
    )))
    if len(matches) > 1:
        raise ValueError("multiple sources match this identity; resolve them manually")
    source = matches[0] if matches else Source(
        id=uuid4(), name=name, source_type=source_type, base_url=base_url,
        status=source_status, configuration=source_configuration,
    )
    if matches:
        source.status = source_status
        source.configuration = source_configuration
    if not matches:
        db.add(source)
    db.flush()

    connector = db.scalar(select(SourceConnector).where(
        SourceConnector.source_id == source.id,
        SourceConnector.connector_type == connector_type,
        SourceConnector.connector_version == connector_version,
    ))
    if connector is None:
        connector = SourceConnector(
            id=uuid4(), source_id=source.id, connector_type=connector_type,
            connector_version=connector_version, status=connector_status,
            configuration=connector_configuration, capabilities=capabilities, limits=limits,
        )
        db.add(connector)
    else:
        connector.status = connector_status
        connector.configuration = connector_configuration
        connector.capabilities = capabilities
        connector.limits = limits

    db.commit()
    return {"source_id": str(source.id), "connector_id": str(connector.id)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", required=True, type=Path)
    args = parser.parse_args()
    config = _object(json.loads(args.config_file.read_text(encoding="utf-8")), "config")
    with SessionLocal() as db:
        result = configure_source(db, config)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
