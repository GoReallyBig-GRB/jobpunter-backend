"""Add schedule run windows and append-only schedule history.

Revision ID: 0080_source_refresh_windows_history
Revises: 0079_discovery_alerts
"""
from alembic import op
import sqlalchemy as sa

revision = "0080_source_refresh_windows_history"
down_revision = "0079_discovery_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_refresh_schedules", sa.Column("window_start_minute", sa.Integer(), nullable=True))
    op.add_column("source_refresh_schedules", sa.Column("window_end_minute", sa.Integer(), nullable=True))
    op.add_column("source_refresh_schedules", sa.Column("window_timezone", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_source_refresh_schedule_window", "source_refresh_schedules",
        "(window_start_minute IS NULL AND window_end_minute IS NULL AND window_timezone IS NULL) OR "
        "(window_start_minute BETWEEN 0 AND 1439 AND window_end_minute BETWEEN 0 AND 1439 "
        "AND window_start_minute <> window_end_minute AND window_timezone IS NOT NULL)",
    )
    op.create_table(
        "source_refresh_schedule_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("source_refresh_schedules.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "schedule_id", "schedule_revision", name="uq_source_refresh_schedule_revision"
        ),
    )
    op.create_index(
        "ix_source_refresh_schedule_events_schedule_created",
        "source_refresh_schedule_events", ["schedule_id", "created_at"],
    )
    op.execute("""
        CREATE FUNCTION reject_source_refresh_schedule_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'source refresh schedule history is append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER source_refresh_schedule_events_no_update_delete
        BEFORE UPDATE OR DELETE ON source_refresh_schedule_events
        FOR EACH ROW EXECUTE FUNCTION reject_source_refresh_schedule_event_mutation();
    """)
    op.execute("""
        CREATE TRIGGER source_refresh_schedule_events_no_truncate
        BEFORE TRUNCATE ON source_refresh_schedule_events
        FOR EACH STATEMENT EXECUTE FUNCTION reject_source_refresh_schedule_event_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS source_refresh_schedule_events_no_truncate ON source_refresh_schedule_events")
    op.execute("DROP TRIGGER IF EXISTS source_refresh_schedule_events_no_update_delete ON source_refresh_schedule_events")
    op.execute("DROP FUNCTION IF EXISTS reject_source_refresh_schedule_event_mutation()")
    op.drop_index(
        "ix_source_refresh_schedule_events_schedule_created",
        table_name="source_refresh_schedule_events",
    )
    op.drop_table("source_refresh_schedule_events")
    op.drop_constraint(
        "ck_source_refresh_schedule_window", "source_refresh_schedules", type_="check"
    )
    op.drop_column("source_refresh_schedules", "window_timezone")
    op.drop_column("source_refresh_schedules", "window_end_minute")
    op.drop_column("source_refresh_schedules", "window_start_minute")
