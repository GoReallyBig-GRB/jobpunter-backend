"""Require published configuration for general discovery schedules."""
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0085_discovery_schedule_governance"
down_revision = "0084_source_refresh_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_schedules", sa.Column("config_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_discovery_schedule_config", "discovery_schedules",
                          "governance_config_versions", ["config_version_id"], ["id"], ondelete="RESTRICT")
    connection = op.get_bind()
    events = sa.table("audit_events", sa.column("id", sa.Uuid()), sa.column("event_type", sa.String()),
                      sa.column("resource_type", sa.String()), sa.column("resource_id", sa.Uuid()),
                      sa.column("actor_type", sa.String()), sa.column("from_status", sa.String()),
                      sa.column("to_status", sa.String()), sa.column("reason", sa.String()),
                      sa.column("details", sa.JSON()), sa.column("occurred_at", sa.DateTime(timezone=True)))
    now = datetime.now(UTC)
    for row in connection.execute(sa.text("SELECT id, revision FROM discovery_schedules WHERE status = 'ACTIVE'")).mappings():
        connection.execute(events.insert().values(
            id=uuid4(), event_type="DISCOVERY_SCHEDULE_GOVERNANCE_CUTOVER",
            resource_type="discovery_schedule", resource_id=row["id"], actor_type="SYSTEM",
            from_status="ACTIVE", to_status="PAUSED", reason="Publish configuration before resuming",
            details={"migration": revision, "previous_revision": row["revision"]}, occurred_at=now,
        ))
    op.execute("""UPDATE discovery_schedules SET status = 'PAUSED', next_run_at = NULL,
        revision = revision + 1 WHERE status = 'ACTIVE'""")
    # Existing runs already have database-enforced immutable snapshots. Preserve
    # those snapshots without inventing a governance version for historical work.


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM governance_config_versions WHERE config_key LIKE 'discovery.schedule.%') THEN
            RAISE EXCEPTION 'Cannot remove discovery schedule governance history';
        END IF;
    END $$;""")
    op.drop_constraint("fk_discovery_schedule_config", "discovery_schedules", type_="foreignkey")
    op.drop_column("discovery_schedules", "config_version_id")
