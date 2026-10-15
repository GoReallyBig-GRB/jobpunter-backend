"""Version source connector configuration and bind each acquisition run to it.

Revision ID: 0047_source_connector_configuration_versions
Revises: 0046_job_discovery_events
"""
from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa

revision = "0047_source_connector_configuration_versions"
down_revision = "0046_job_discovery_events"
branch_labels = None
depends_on = None


def _fingerprint(configuration: object) -> str:
    canonical = json.dumps(configuration if isinstance(configuration, dict) else {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column(
        "source_connectors",
        sa.Column("active_configuration_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "acquisition_runs",
        sa.Column("configuration_version_id", sa.Uuid(), nullable=True),
    )
    op.create_table(
        "source_connector_configuration_versions",
        sa.Column("source_connector_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_connector_id"], ["source_connectors.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_connector_configuration_versions"),
        sa.UniqueConstraint(
            "source_connector_id", "version",
            name="uq_source_connector_configuration_version",
        ),
    )

    connection = op.get_bind()
    connectors = connection.execute(
        sa.text("SELECT id, configuration FROM source_connectors ORDER BY id")
    ).mappings()
    from uuid import uuid4
    from datetime import datetime, timezone

    for connector in connectors:
        configuration = connector["configuration"] or {}
        connection.execute(
            sa.text(
                """
                INSERT INTO source_connector_configuration_versions
                    (id, source_connector_id, version, configuration,
                     configuration_fingerprint, status, created_by_user_id,
                     reason, created_at)
                VALUES
                    (:id, :connector_id, 1, CAST(:configuration AS JSON), :fingerprint,
                     'PUBLISHED', NULL, 'Legacy configuration snapshot during migration',
                     :created_at)
                """
            ),
            {
                "id": uuid4(),
                "connector_id": connector["id"],
                "configuration": json.dumps(configuration),
                "fingerprint": _fingerprint(configuration),
                "created_at": datetime.now(timezone.utc),
            },
        )

    connection.execute(
        sa.text(
            """
            UPDATE acquisition_runs AS run
            SET configuration_version_id = version_record.id
            FROM source_connector_configuration_versions AS version_record
            WHERE version_record.source_connector_id = run.source_connector_id
              AND version_record.version = 1
            """
        )
    )
    op.create_foreign_key(
        "fk_acquisition_run_connector_configuration_version",
        "acquisition_runs",
        "source_connector_configuration_versions",
        ["configuration_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_acquisition_run_connector_configuration_version",
        "acquisition_runs",
        type_="foreignkey",
    )
    op.drop_table("source_connector_configuration_versions")
    op.drop_column("acquisition_runs", "configuration_version_id")
    op.drop_column("source_connectors", "active_configuration_version")
