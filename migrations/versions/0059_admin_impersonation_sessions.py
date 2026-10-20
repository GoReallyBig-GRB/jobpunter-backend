"""Persist bounded, auditable read-only Admin inspection sessions.

Revision ID: 0059_admin_impersonation_sessions
Revises: 0058_manual_job_provenance_owner
"""
from alembic import op
import sqlalchemy as sa

revision = "0059_admin_impersonation_sessions"
down_revision = "0058_manual_job_provenance_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_impersonation_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admin_user_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_admin_impersonation_sessions"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ENDED', 'EXPIRED', 'REVOKED')",
            name="ck_admin_impersonation_sessions_status",
        ),
        sa.CheckConstraint("expires_at > started_at", name="ck_admin_impersonation_expiry"),
        sa.CheckConstraint(
            "admin_user_id <> target_user_id",
            name="ck_admin_impersonation_distinct_users",
        ),
    )
    op.create_index(
        "ix_admin_impersonation_admin_status",
        "admin_impersonation_sessions",
        ["admin_user_id", "status", "started_at"],
    )
    op.create_index(
        "ix_admin_impersonation_target_started",
        "admin_impersonation_sessions",
        ["target_user_id", "started_at"],
    )
    op.create_index(
        "uq_admin_impersonation_one_active_per_admin_target",
        "admin_impersonation_sessions",
        ["admin_user_id", "target_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_admin_impersonation_one_active_per_admin_target",
        table_name="admin_impersonation_sessions",
    )
    op.drop_index(
        "ix_admin_impersonation_target_started",
        table_name="admin_impersonation_sessions",
    )
    op.drop_index(
        "ix_admin_impersonation_admin_status",
        table_name="admin_impersonation_sessions",
    )
    op.drop_table("admin_impersonation_sessions")
