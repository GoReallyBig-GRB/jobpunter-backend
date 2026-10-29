"""Add versioned admin-managed SMTP provider configurations.

Revision ID: 0071_smtp_provider_configurations
Revises: 0070_source_operational_state_contract
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0071_smtp_provider_configurations"
down_revision = "0070_source_operational_state_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "smtp_provider_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"),
        sa.Column("provider_name", sa.String(length=80), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("use_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("username", sa.String(length=320), nullable=True),
        sa.Column("encrypted_password", sa.Text(), nullable=True),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("event_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("retry_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("retry_backoff_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("smtp_provider_configurations.id", ondelete="RESTRICT"), nullable=True),
        sa.UniqueConstraint("version", name="uq_smtp_provider_configuration_version"),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_smtp_provider_port"),
        sa.CheckConstraint("retry_limit BETWEEN 0 AND 20", name="ck_smtp_provider_retry_limit"),
        sa.CheckConstraint("retry_backoff_seconds BETWEEN 1 AND 86400", name="ck_smtp_provider_retry_backoff"),
        sa.CheckConstraint("status IN ('DRAFT', 'PUBLISHED', 'RETIRED')", name="ck_smtp_provider_status"),
    )
    op.create_index(
        "uq_smtp_provider_configuration_published",
        "smtp_provider_configurations", ["status"], unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_index(
        "ix_smtp_provider_configuration_status_created",
        "smtp_provider_configurations", ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_smtp_provider_configuration_status_created", table_name="smtp_provider_configurations")
    op.drop_index("uq_smtp_provider_configuration_published", table_name="smtp_provider_configurations")
    op.drop_table("smtp_provider_configurations")
