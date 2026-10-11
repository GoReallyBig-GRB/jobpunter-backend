"""Track source permission review and observed operational health.

Revision ID: 0028_source_governance_health
Revises: 0027_ai_policy_version_trace
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_source_governance_health"
down_revision = "0027_ai_policy_version_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("permission_review_status", sa.String(length=32), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "sources",
        sa.Column("permission_review_reference", sa.Text(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("permission_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("operational_state", sa.String(length=32), nullable=False, server_default="UNAVAILABLE"),
    )
    op.add_column(
        "sources",
        sa.Column("last_error_category", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sources", "last_error_category")
    op.drop_column("sources", "operational_state")
    op.drop_column("sources", "permission_reviewed_at")
    op.drop_column("sources", "permission_review_reference")
    op.drop_column("sources", "permission_review_status")
