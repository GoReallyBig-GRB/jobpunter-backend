"""Add normalized discovery targets and lifecycle history.

Revision ID: 0064_discovery_targets
Revises: 0063_worker_heartbeats
"""
from alembic import op
import sqlalchemy as sa

revision = "0064_discovery_targets"
down_revision = "0063_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_connector_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="DRAFT", nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("target_kind IN ('CRAWL', 'SEARCH')", name="ck_discovery_target_kind"),
        sa.CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'RETIRED')", name="ck_discovery_target_status"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_connector_id"], ["source_connectors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_targets"),
        sa.UniqueConstraint("source_connector_id", "url", "target_kind", "purpose",
                            name="uq_discovery_target_connector_url_kind_purpose"),
    )
    op.create_index("ix_discovery_targets_status_kind", "discovery_targets", ["status", "target_kind"])
    op.create_index("ix_discovery_targets_source_status", "discovery_targets", ["source_id", "status"])
    op.create_table(
        "discovery_target_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("previous_status", sa.String(length=16), nullable=True),
        sa.Column("new_status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('CREATED', 'ACTIVATED', 'PAUSED', 'RETIRED')",
                           name="ck_discovery_target_event_action"),
        sa.ForeignKeyConstraint(["target_id"], ["discovery_targets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_target_events"),
    )
    op.create_index("ix_discovery_target_events_target_created", "discovery_target_events",
                    ["target_id", "created_at"])
    op.execute("""
        CREATE TRIGGER discovery_target_events_append_only_rows
        BEFORE UPDATE OR DELETE ON discovery_target_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()
    """)
    op.execute("""
        CREATE TRIGGER discovery_target_events_append_only_truncate
        BEFORE TRUNCATE ON discovery_target_events
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS discovery_target_events_append_only_truncate ON discovery_target_events")
    op.execute("DROP TRIGGER IF EXISTS discovery_target_events_append_only_rows ON discovery_target_events")
    op.drop_index("ix_discovery_target_events_target_created", table_name="discovery_target_events")
    op.drop_table("discovery_target_events")
    op.drop_index("ix_discovery_targets_source_status", table_name="discovery_targets")
    op.drop_index("ix_discovery_targets_status_kind", table_name="discovery_targets")
    op.drop_table("discovery_targets")
