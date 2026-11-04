"""Add durable administrator discovery schedules and runs.

Revision ID: 0075_discovery_schedules
Revises: 0074_discovery_strategies
"""
import sqlalchemy as sa
from alembic import op

revision = "0075_discovery_schedules"
down_revision = "0074_discovery_strategies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("schedule_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("frequency_type", sa.String(16), nullable=False),
        sa.Column("frequency_definition", sa.JSON(), nullable=False),
        sa.Column("window_definition", sa.JSON(), nullable=False),
        sa.Column("source_scope", sa.JSON(), nullable=False),
        sa.Column("target_scope", sa.JSON(), nullable=False),
        sa.Column("market_scope", sa.JSON(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("overlap_policy", sa.String(16), nullable=False),
        sa.Column("maximum_concurrent_runs", sa.Integer(), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("retry_policy", sa.JSON(), nullable=False),
        sa.Column("manual_execution_enabled", sa.Boolean(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True)),
        sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("schedule_type IN ('GLOBAL_SWEEP','SOURCE_REFRESH','TARGET_REFRESH','MARKET_DISCOVERY','HEALTH_CHECK','STALENESS_REVALIDATION')", name="ck_discovery_schedule_type"),
        sa.CheckConstraint("status IN ('ACTIVE','PAUSED','RETIRED')", name="ck_discovery_schedule_status"),
        sa.CheckConstraint("frequency_type IN ('INTERVAL','DAILY','WEEKLY')", name="ck_discovery_schedule_frequency_type"),
        sa.CheckConstraint("overlap_policy IN ('SKIP','QUEUE')", name="ck_discovery_schedule_overlap_policy"),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["discovery_strategy_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_schedules"),
    )
    op.create_index("ix_discovery_schedules_due", "discovery_schedules", ["status", "next_run_at", "priority"])
    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_key", sa.String(255), nullable=False),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("schedule_id", sa.Uuid()),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("operation_counts", sa.JSON(), nullable=False),
        sa.Column("record_counts", sa.JSON(), nullable=False),
        sa.Column("budget_outcome", sa.JSON(), nullable=False),
        sa.Column("retry_information", sa.JSON(), nullable=False),
        sa.Column("error_details", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid()),
        sa.Column("reason", sa.Text()),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("trigger_type IN ('SCHEDULED','MANUAL','RETRY')", name="ck_discovery_run_trigger_type"),
        sa.CheckConstraint("status IN ('PENDING','RUNNING','SUCCEEDED','PARTIAL','FAILED','BLOCKED','CANCELLED')", name="ck_discovery_run_status"),
        sa.ForeignKeyConstraint(["schedule_id"], ["discovery_schedules.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["discovery_strategy_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_runs"),
        sa.UniqueConstraint("run_key", name="uq_discovery_run_key"),
    )
    op.create_index("ix_discovery_runs_status_created", "discovery_runs", ["status", "created_at"])
    op.create_index("ix_discovery_runs_schedule_created", "discovery_runs", ["schedule_id", "created_at"])
    op.create_table(
        "discovery_role_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("role_key", sa.String(64), nullable=False),
        sa.Column("role_order", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("eligibility", sa.JSON(), nullable=False),
        sa.Column("selected_tool_key", sa.String(80)),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("target_id", sa.Uuid()),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("input_scope", sa.JSON(), nullable=False),
        sa.Column("record_counts", sa.JSON(), nullable=False),
        sa.Column("budget_usage", sa.JSON(), nullable=False),
        sa.Column("fallback_decision", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(80)),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('PENDING','RUNNING','SUCCEEDED','PARTIAL','SKIPPED','FAILED','BLOCKED')", name="ck_discovery_role_execution_outcome"),
        sa.ForeignKeyConstraint(["run_id"], ["discovery_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["discovery_strategy_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_id"], ["discovery_targets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_role_executions"),
        sa.UniqueConstraint("run_id", "role_key", "attempt_number", name="uq_discovery_role_run_attempt"),
    )
    op.create_index("ix_discovery_role_executions_run_order", "discovery_role_executions", ["run_id", "role_order"])
    op.execute("""
        CREATE FUNCTION reject_discovery_run_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.run_key IS DISTINCT FROM OLD.run_key OR
               NEW.run_type IS DISTINCT FROM OLD.run_type OR
               NEW.trigger_type IS DISTINCT FROM OLD.trigger_type OR
               NEW.schedule_id IS DISTINCT FROM OLD.schedule_id OR
               NEW.strategy_version_id IS DISTINCT FROM OLD.strategy_version_id OR
               NEW.configuration_snapshot::text IS DISTINCT FROM OLD.configuration_snapshot::text OR
               NEW.configuration_hash IS DISTINCT FROM OLD.configuration_hash OR
               NEW.scope::text IS DISTINCT FROM OLD.scope::text OR
               NEW.requested_by_user_id IS DISTINCT FROM OLD.requested_by_user_id OR
               NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'discovery run configuration snapshot is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER discovery_run_snapshot_immutable
        BEFORE UPDATE ON discovery_runs
        FOR EACH ROW EXECUTE FUNCTION reject_discovery_run_snapshot_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS discovery_run_snapshot_immutable ON discovery_runs")
    op.execute("DROP FUNCTION IF EXISTS reject_discovery_run_snapshot_mutation()")
    op.drop_index("ix_discovery_role_executions_run_order", table_name="discovery_role_executions")
    op.drop_table("discovery_role_executions")
    op.drop_index("ix_discovery_runs_schedule_created", table_name="discovery_runs")
    op.drop_index("ix_discovery_runs_status_created", table_name="discovery_runs")
    op.drop_table("discovery_runs")
    op.drop_index("ix_discovery_schedules_due", table_name="discovery_schedules")
    op.drop_table("discovery_schedules")
