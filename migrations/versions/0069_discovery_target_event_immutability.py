"""Enforce append-only semantics for discovery-target lifecycle history.

Revision ID: 0069_discovery_target_event_immutability
Revises: 0068_source_permission_evidence
"""
from alembic import op

revision = "0069_discovery_target_event_immutability"
down_revision = "0068_source_permission_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 0064 already creates these triggers on fresh installs. Keep this
    # revision safe for both fresh databases and databases where a trigger was
    # removed after 0064: only create a trigger when it is actually missing.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgname = 'discovery_target_events_append_only_rows'
                  AND tgrelid = 'discovery_target_events'::regclass
                  AND NOT tgisinternal
            ) THEN
                CREATE TRIGGER discovery_target_events_append_only_rows
                BEFORE UPDATE OR DELETE ON discovery_target_events
                FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation();
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgname = 'discovery_target_events_append_only_truncate'
                  AND tgrelid = 'discovery_target_events'::regclass
                  AND NOT tgisinternal
            ) THEN
                CREATE TRIGGER discovery_target_events_append_only_truncate
                BEFORE TRUNCATE ON discovery_target_events
                FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation();
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Keep the shared function: existing audit-event triggers still depend on it.
    op.execute(
        "DROP TRIGGER IF EXISTS discovery_target_events_append_only_truncate "
        "ON discovery_target_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS discovery_target_events_append_only_rows "
        "ON discovery_target_events"
    )
