"""Persist Admin-managed forbidden target policy rules.

Revision ID: 0060_forbidden_target_rules
Revises: 0059_admin_impersonation_sessions
"""
from alembic import op
import sqlalchemy as sa

revision = "0060_forbidden_target_rules"
down_revision = "0059_admin_impersonation_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forbidden_target_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_type", sa.String(length=24), nullable=False),
        sa.Column("pattern", sa.String(length=2048), nullable=False),
        sa.Column("connector_scope", sa.String(length=32), server_default="ALL", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rule_type IN ('HOST_EXACT', 'HOST_SUFFIX', 'URL_EXACT', 'URL_PATH_PREFIX')",
            name="ck_forbidden_target_rule_type",
        ),
        sa.CheckConstraint(
            "connector_scope IN ('ALL', 'SCRAPY', 'PLAYWRIGHT', 'JSON_API', 'RSS_ATOM', 'JOBSPY', 'GREENHOUSE_JOB_BOARD')",
            name="ck_forbidden_target_connector_scope",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_forbidden_target_rules"),
        sa.UniqueConstraint("rule_type", "pattern", "connector_scope", name="uq_forbidden_target_rule_identity"),
    )
    op.create_index(
        "ix_forbidden_target_rules_active_scope",
        "forbidden_target_rules",
        ["active", "connector_scope"],
    )
    op.create_index(
        "ix_forbidden_target_rules_type_pattern",
        "forbidden_target_rules",
        ["rule_type", "pattern"],
    )


def downgrade() -> None:
    op.drop_index("ix_forbidden_target_rules_type_pattern", table_name="forbidden_target_rules")
    op.drop_index("ix_forbidden_target_rules_active_scope", table_name="forbidden_target_rules")
    op.drop_table("forbidden_target_rules")
