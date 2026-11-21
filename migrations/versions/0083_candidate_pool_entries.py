"""Add candidate pool entries synced from the central job pool.

Revision ID: 0083_candidate_pool_entries
Revises: 0082_resilient_discovery
"""

import sqlalchemy as sa
from alembic import op

revision = "0083_candidate_pool_entries"
down_revision = "0082_resilient_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_pool_entries",
        sa.Column("candidate_profile_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("match_basis", sa.JSON(), nullable=False),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name="ck_candidate_pool_status"),
        sa.ForeignKeyConstraint(["candidate_profile_id"], ["candidate_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_profile_id", "job_id", name="uq_candidate_pool_candidate_job"),
    )
    op.create_index(
        "ix_candidate_pool_candidate_status_matched", "candidate_pool_entries",
        ["candidate_profile_id", "status", "matched_at"],
    )
    op.create_index("ix_candidate_pool_job", "candidate_pool_entries", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_candidate_pool_job", table_name="candidate_pool_entries")
    op.drop_index("ix_candidate_pool_candidate_status_matched", table_name="candidate_pool_entries")
    op.drop_table("candidate_pool_entries")
