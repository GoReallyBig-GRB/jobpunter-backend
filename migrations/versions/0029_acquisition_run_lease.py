"""Add expiry timestamps for recoverable acquisition-run leases.

Revision ID: 0029_acquisition_run_lease
Revises: 0028_source_governance_health
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_acquisition_run_lease"
down_revision = "0028_source_governance_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "acquisition_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE acquisition_runs
        SET lease_expires_at = started_at + INTERVAL '1 hour'
        WHERE status = 'RUNNING'
          AND started_at IS NOT NULL
          AND lease_expires_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("acquisition_runs", "lease_expires_at")
