"""Add admin grants, versioned configuration and audit events.
Revision ID: 0015_admin_governance_foundation
Revises: 0014_evaluation_input_provenance
"""
from alembic import op
import sqlalchemy as sa
revision = "0015_admin_governance_foundation"
down_revision = "0014_evaluation_input_provenance"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("admin_grants",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="ADMIN"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("granted_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "role", name="uq_admin_grant_user_role"))
    op.create_index("ix_admin_grants_active_role", "admin_grants", ["active", "role"])
    op.create_table("governance_config_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("config_key", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("governance_config_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.UniqueConstraint("config_key", "version", name="uq_governance_config_key_version"))
    op.create_index("ix_governance_config_key_status", "governance_config_versions", ["config_key", "status"])
    op.create_table("admin_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_admin_audit_actor_created", "admin_audit_events", ["actor_user_id", "created_at"])
    op.create_index("ix_admin_audit_resource", "admin_audit_events", ["resource_type", "resource_id"])

def downgrade() -> None:
    op.drop_index("ix_admin_audit_resource", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_actor_created", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
    op.drop_index("ix_governance_config_key_status", table_name="governance_config_versions")
    op.drop_table("governance_config_versions")
    op.drop_index("ix_admin_grants_active_role", table_name="admin_grants")
    op.drop_table("admin_grants")
