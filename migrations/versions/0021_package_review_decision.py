"""Record who decided a package review and when.

Revision ID: 0021_package_review_decision
Revises: 0020_package_source_hash
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_package_review_decision"
down_revision = "0020_package_source_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_packages",
        sa.Column("review_decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "application_packages",
        sa.Column(
            "review_decided_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("application_packages", "review_decided_by_user_id")
    op.drop_column("application_packages", "review_decided_at")
