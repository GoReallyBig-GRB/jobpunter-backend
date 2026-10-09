"""Add outbox processing lease for abandoned-work recovery.

Revision ID: 0006_outbox_processing_lease
Revises: 0005_job_resolution_candidates
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_outbox_processing_lease"
down_revision = "0005_job_resolution_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox_events", "locked_at")
