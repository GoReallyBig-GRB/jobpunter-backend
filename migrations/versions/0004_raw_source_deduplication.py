"""Preserve raw source record observations across content changes.

Revision ID: 0004_raw_source_deduplication
Revises: 0003_jobs_acquisition
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_raw_source_deduplication"
down_revision = "0003_jobs_acquisition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_source_records",
        sa.Column("deduplication_key", sa.String(600), nullable=True),
    )
    op.execute(
        """
        UPDATE raw_source_records
        SET deduplication_key = CASE
            WHEN external_record_id IS NOT NULL THEN 'id:' || external_record_id
            ELSE 'hash:' || content_hash
        END
        """
    )
    op.alter_column("raw_source_records", "deduplication_key", nullable=False)
    op.drop_constraint("uq_raw_source_external_id", "raw_source_records", type_="unique")
    op.create_unique_constraint(
        "uq_raw_source_dedup_key",
        "raw_source_records",
        ["source_id", "deduplication_key", "content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_raw_source_dedup_key", "raw_source_records", type_="unique")
    op.create_unique_constraint(
        "uq_raw_source_external_id",
        "raw_source_records",
        ["source_id", "external_record_id"],
    )
    op.drop_column("raw_source_records", "deduplication_key")
