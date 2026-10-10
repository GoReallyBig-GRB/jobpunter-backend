"""Create versioned application packages and artifact provenance.

Revision ID: 0017_application_package_versioning
Revises: 0016_ai_execution_telemetry
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_application_package_versioning"
down_revision = "0016_ai_execution_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_packages",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("application_id", "version_number", name="uq_application_package_version"),
    )
    op.create_index(
        "ix_application_packages_application_created",
        "application_packages", ["application_id", "created_at"],
    )
    op.alter_column("application_packages", "status", server_default=None)

    op.create_table(
        "application_package_items",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("package_id", sa.Uuid(), sa.ForeignKey("application_packages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("artifact_type", sa.String(40), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("unsupported_claims", sa.JSON(), nullable=False),
        sa.Column("ai_execution_id", sa.Uuid(), sa.ForeignKey("ai_executions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("package_id", "artifact_type", name="uq_application_package_item_type"),
    )
    op.create_index("ix_application_package_items_package", "application_package_items", ["package_id"])
    op.alter_column("application_package_items", "confidence", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_application_package_items_package", table_name="application_package_items")
    op.drop_table("application_package_items")
    op.drop_index("ix_application_packages_application_created", table_name="application_packages")
    op.drop_table("application_packages")
