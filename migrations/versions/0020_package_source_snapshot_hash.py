"""Protect immutable application-package source snapshots with a canonical hash.

Revision ID: 0020_package_source_snapshot_hash
Revises: 0019_ai_output_contract_version
"""
import hashlib
import json

from alembic import op
import sqlalchemy as sa

revision = "0020_package_source_hash"
down_revision = "0019_ai_output_contract_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_packages",
        sa.Column("source_snapshot_hash", sa.String(64), nullable=True),
    )
    bind = op.get_bind()
    packages = sa.Table(
        "application_packages",
        sa.MetaData(),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64)),
    )
    rows = bind.execute(sa.select(packages.c.id, packages.c.source_snapshot)).mappings()
    for row in rows:
        canonical = json.dumps(
            row["source_snapshot"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        bind.execute(
            packages.update()
            .where(packages.c.id == row["id"])
            .values(source_snapshot_hash=digest)
        )
    op.alter_column("application_packages", "source_snapshot_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("application_packages", "source_snapshot_hash")
