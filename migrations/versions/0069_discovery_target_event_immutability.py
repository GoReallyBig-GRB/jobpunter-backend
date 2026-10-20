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
    # Reuse the shared trigger function installed by 0061. It raises SQLSTATE
    # 55000 for UPDATE, DELETE, and TRUNCATE attempts without mutating history.
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
    # Keep the shared function: existing audit-event triggers still depend on it.
    op.execute(
        "DROP TRIGGER IF EXISTS discovery_target_events_append_only_truncate "
        "ON discovery_target_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS discovery_target_events_append_only_rows "
        "ON discovery_target_events"
    )
