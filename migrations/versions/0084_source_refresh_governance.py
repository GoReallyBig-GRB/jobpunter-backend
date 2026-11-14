"""Pin published source refresh configuration to schedules and queued occurrences."""
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0084_source_refresh_governance"
down_revision = "0083_protected_system_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Do not invent historical configuration versions for already-running work.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM work_items WHERE work_type = 'SOURCE_REFRESH' AND status = 'PROCESSING') THEN
            RAISE EXCEPTION 'Drain or recover processing source refresh work before upgrading';
        END IF;
    END $$;""")
    op.add_column("source_refresh_schedules", sa.Column("config_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_refresh_schedule_config", "source_refresh_schedules",
                          "governance_config_versions", ["config_version_id"], ["id"], ondelete="RESTRICT")
    op.add_column("work_items", sa.Column("source_refresh_config_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_refresh_work_config", "work_items", "governance_config_versions",
                          ["source_refresh_config_version_id"], ["id"], ondelete="RESTRICT")
    connection = op.get_bind()
    now = datetime.now(UTC)
    events = sa.table("source_refresh_schedule_events",
                      sa.column("id", sa.Uuid()), sa.column("schedule_id", sa.Uuid()),
                      sa.column("schedule_revision", sa.Integer()), sa.column("action", sa.String()),
                      sa.column("reason", sa.String()), sa.column("before", sa.JSON()),
                      sa.column("after", sa.JSON()), sa.column("created_at", sa.DateTime(timezone=True)))
    # Preserve legacy settings/history; require an actual administrator to publish
    # their imported settings rather than fabricating a user-authored publication.
    for row in connection.execute(sa.text("SELECT * FROM source_refresh_schedules WHERE status = 'ACTIVE'")).mappings():
        def clock(value):
            return None if value is None else f"{value // 60:02d}:{value % 60:02d}"

        before = {"status": row["status"], "interval_minutes": row["interval_minutes"],
                  "window_start": clock(row["window_start_minute"]),
                  "window_end": clock(row["window_end_minute"]), "window_timezone": row["window_timezone"],
                  "config_version_id": None}
        connection.execute(events.insert().values(
            id=uuid4(), schedule_id=row["id"], schedule_revision=row["revision"] + 1,
            action="MIGRATED", reason="Governance publication required after upgrade",
            before=before, after={**before, "status": "PAUSED"}, created_at=now,
        ))
    op.execute("""UPDATE source_refresh_schedules SET status = 'PAUSED',
        status_reason = 'Governance publication required after upgrade', revision = revision + 1
        WHERE status = 'ACTIVE'""")
    op.execute("""UPDATE work_items SET status = 'CANCELLED', completed_at = CURRENT_TIMESTAMP,
        revision = revision + 1, result = '{"outcome":"CANCELLED","reason":"GOVERNANCE_CUTOVER"}'::json
        WHERE work_type = 'SOURCE_REFRESH' AND status = 'PENDING'""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM governance_config_versions WHERE config_key LIKE 'discovery.source_refresh.%') THEN
            RAISE EXCEPTION 'Cannot remove source refresh governance history';
        END IF;
    END $$;""")
    op.drop_constraint("fk_refresh_work_config", "work_items", type_="foreignkey")
    op.drop_column("work_items", "source_refresh_config_version_id")
    op.drop_constraint("fk_refresh_schedule_config", "source_refresh_schedules", type_="foreignkey")
    op.drop_column("source_refresh_schedules", "config_version_id")
