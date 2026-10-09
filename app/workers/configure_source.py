"""Validate and idempotently configure one acquisition source and connector.

Example:
  python -m app.workers.configure_source --config source.json
  python -m app.workers.configure_source --config source.json --apply

The default is validation-only. Configuration JSON must not contain secrets; use environment
variables or a secret manager for credentials.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Source, SourceConnector
from app.infrastructure.db.session import SessionLocal


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=64)
    base_url: str | None = None
    status: str = "UNKNOWN"
    configuration: dict[str, Any] = Field(default_factory=dict)


class ConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connector_type: str = Field(min_length=1, max_length=64)
    connector_version: str = Field(min_length=1, max_length=64)
    status: str = "UNKNOWN"
    configuration: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)


class SourceSetupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceConfig
    connector: ConnectorConfig


def configure_source(db: Session, config: SourceSetupConfig) -> tuple[Source, SourceConnector, bool]:
    """Create or update one exact source identity and its versioned connector."""
    with db.begin():
        source_matches = list(
            db.scalars(
                select(Source).where(
                    Source.name == config.source.name,
                    Source.source_type == config.source.source_type,
                    Source.base_url == config.source.base_url,
                )
            )
        )
        if len(source_matches) > 1:
            raise ValueError("multiple sources match this identity; resolve duplicates before configuring")
        created = not source_matches
        source = source_matches[0] if source_matches else Source(
            name=config.source.name,
            source_type=config.source.source_type,
            base_url=config.source.base_url,
            status=config.source.status,
            configuration=config.source.configuration,
        )
        if created:
            db.add(source)
            db.flush()
        else:
            source.status = config.source.status
            source.configuration = config.source.configuration

        connector = db.scalar(
            select(SourceConnector).where(
                SourceConnector.source_id == source.id,
                SourceConnector.connector_type == config.connector.connector_type.upper(),
                SourceConnector.connector_version == config.connector.connector_version,
            )
        )
        if connector is None:
            connector = SourceConnector(
                source_id=source.id,
                connector_type=config.connector.connector_type.upper(),
                connector_version=config.connector.connector_version,
                status=config.connector.status,
                configuration=config.connector.configuration,
                capabilities=config.connector.capabilities,
                limits=config.connector.limits,
            )
            db.add(connector)
        else:
            connector.status = config.connector.status
            connector.configuration = config.connector.configuration
            connector.capabilities = config.connector.capabilities
            connector.limits = config.connector.limits
        db.flush()
    return source, connector, created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="JSON source/connector configuration")
    parser.add_argument("--apply", action="store_true", help="persist changes; without this flag only validate")
    args = parser.parse_args()

    try:
        config = SourceSetupConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        parser.error(f"invalid configuration: {exc}")

    if not args.apply:
        print(json.dumps({
            "valid": True,
            "applied": False,
            "source": config.source.name,
            "source_type": config.source.source_type,
            "connector_type": config.connector.connector_type.upper(),
            "connector_version": config.connector.connector_version,
            "message": "Validation passed. Re-run with --apply to persist.",
        }, sort_keys=True))
        return

    with SessionLocal() as db:
        source, connector, created = configure_source(db, config)
        print(json.dumps({
            "valid": True,
            "applied": True,
            "source_id": str(source.id),
            "connector_id": str(connector.id),
            "source_created": created,
            "connector_type": connector.connector_type,
            "connector_version": connector.connector_version,
            "status": connector.status,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
