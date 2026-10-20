"""Enforce append-only semantics for persisted audit histories.

Revision ID: 0061_immutable_audit_events
Revises: 0060_forbidden_target_rules
"""
from alembic import op

revision = "0061_immutable_audit_events"
down_revision = "0060_forbidden_target_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION reject_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit event history is append-only'
                USING ERRCODE = '55000';
        END;
        $$
    """)
    for table in ("admin_audit_events", "audit_events"):
        op.execute(f"""
            CREATE TRIGGER {table}_append_only_rows
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()
        """)
        op.execute(f"""
            CREATE TRIGGER {table}_append_only_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation()
        """)


def downgrade() -> None:
    for table in ("admin_audit_events", "audit_events"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only_rows ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_event_mutation()")
