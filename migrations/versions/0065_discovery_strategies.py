"""Add versioned discovery strategies.

Revision ID: 0065_discovery_strategies
Revises: 0064_discovery_targets
"""
from alembic import op
import sqlalchemy as sa

revision = "0065_discovery_strategies"
down_revision = "0064_discovery_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_strategies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('ENABLED', 'DISABLED', 'RETIRED')", name="ck_discovery_strategy_status"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_strategies"),
        sa.UniqueConstraint("strategy_key", name="uq_discovery_strategy_key"),
    )
    op.create_index("ix_discovery_strategies_status_key", "discovery_strategies", ["status", "strategy_key"])
    op.create_table(
        "discovery_strategy_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("validation_status", sa.String(length=16), nullable=False),
        sa.Column("validation_result", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previous_version_id", sa.Uuid(), nullable=True),
        sa.Column("rollback_of_version_id", sa.Uuid(), nullable=True),
        sa.Column("audit_event_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("status IN ('DRAFT', 'VALIDATED', 'PUBLISHED', 'SUPERSEDED', 'RETIRED')", name="ck_discovery_strategy_version_status"),
        sa.CheckConstraint("validation_status IN ('NOT_RUN', 'VALID', 'INVALID')", name="ck_discovery_strategy_validation_status"),
        sa.ForeignKeyConstraint(["strategy_id"], ["discovery_strategies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_version_id"], ["discovery_strategy_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rollback_of_version_id"], ["discovery_strategy_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["audit_event_id"], ["admin_audit_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_strategy_versions"),
        sa.UniqueConstraint("strategy_id", "version", name="uq_discovery_strategy_version"),
    )
    op.create_index("ix_discovery_strategy_versions_strategy_status", "discovery_strategy_versions", ["strategy_id", "status"])
    op.create_index("uq_discovery_strategy_one_published", "discovery_strategy_versions", ["strategy_id"], unique=True, postgresql_where=sa.text("status = 'PUBLISHED'"))
    op.execute("""
        CREATE FUNCTION reject_published_strategy_content_mutation() RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('PUBLISHED', 'SUPERSEDED') AND (
                NEW.payload::text IS DISTINCT FROM OLD.payload::text OR
                NEW.configuration_hash IS DISTINCT FROM OLD.configuration_hash OR
                NEW.schema_version IS DISTINCT FROM OLD.schema_version OR
                NEW.strategy_id IS DISTINCT FROM OLD.strategy_id OR
                NEW.version IS DISTINCT FROM OLD.version OR
                NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id OR
                NEW.created_at IS DISTINCT FROM OLD.created_at
            ) THEN
                RAISE EXCEPTION 'published discovery strategy content is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER discovery_strategy_published_content_immutable
        BEFORE UPDATE ON discovery_strategy_versions
        FOR EACH ROW EXECUTE FUNCTION reject_published_strategy_content_mutation();
    """)
    op.execute("""
        CREATE FUNCTION reject_discovery_strategy_version_history_removal() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'discovery strategy version history is append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER discovery_strategy_version_no_delete
        BEFORE DELETE ON discovery_strategy_versions
        FOR EACH ROW EXECUTE FUNCTION reject_discovery_strategy_version_history_removal();
    """)
    op.execute("""
        CREATE TRIGGER discovery_strategy_version_no_truncate
        BEFORE TRUNCATE ON discovery_strategy_versions
        FOR EACH STATEMENT EXECUTE FUNCTION reject_discovery_strategy_version_history_removal();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS discovery_strategy_version_no_truncate ON discovery_strategy_versions")
    op.execute("DROP TRIGGER IF EXISTS discovery_strategy_version_no_delete ON discovery_strategy_versions")
    op.execute("DROP FUNCTION IF EXISTS reject_discovery_strategy_version_history_removal()")
    op.execute("DROP TRIGGER IF EXISTS discovery_strategy_published_content_immutable ON discovery_strategy_versions")
    op.execute("DROP FUNCTION IF EXISTS reject_published_strategy_content_mutation()")
    op.drop_index("uq_discovery_strategy_one_published", table_name="discovery_strategy_versions")
    op.drop_index("ix_discovery_strategy_versions_strategy_status", table_name="discovery_strategy_versions")
    op.drop_table("discovery_strategy_versions")
    op.drop_index("ix_discovery_strategies_status_key", table_name="discovery_strategies")
    op.drop_table("discovery_strategies")
