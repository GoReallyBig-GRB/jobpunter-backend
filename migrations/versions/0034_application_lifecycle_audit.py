"""Add unified append-only audit history and application transition rationale.

Revision ID: 0034_application_lifecycle_audit
Revises: 0033_evaluation_overrides
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0034_application_lifecycle_audit"
down_revision = "0033_evaluation_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_events", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column("application_events", sa.Column("notes", sa.Text(), nullable=True))

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_audit_events_resource_occurred",
        "audit_events",
        ["resource_type", "resource_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_actor_occurred",
        "audit_events",
        ["actor_type", "actor_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_event_occurred",
        "audit_events",
        ["event_type", "occurred_at"],
    )

    op.execute("""
        INSERT INTO audit_events (
            id, event_type, resource_type, resource_id, actor_type, actor_id,
            from_status, to_status, reason, notes, details, request_id, occurred_at
        )
        SELECT gen_random_uuid(), event_type, 'application', application_id,
               actor_type, actor_id, from_status, to_status, reason, notes,
               COALESCE(metadata, '{}'::json), NULL, occurred_at
        FROM application_events
    """)
    op.execute("""
        INSERT INTO audit_events (
            id, event_type, resource_type, resource_id, actor_type, actor_id,
            from_status, to_status, reason, notes, details, request_id, occurred_at
        )
        SELECT gen_random_uuid(), action, resource_type, resource_id,
               'USER', actor_user_id, NULL, NULL, NULL, NULL,
               json_build_object('outcome', outcome, 'details', details),
               request_id, created_at
        FROM admin_audit_events
    """)

    op.execute("""
        CREATE FUNCTION prevent_audit_history_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit history is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_history_mutation()
    """)

    op.execute("""
        CREATE FUNCTION mirror_application_event_to_audit()
        RETURNS trigger AS $$
        BEGIN
            INSERT INTO audit_events (
                id, event_type, resource_type, resource_id, actor_type, actor_id,
                from_status, to_status, reason, notes, details, request_id, occurred_at
            ) VALUES (
                gen_random_uuid(), NEW.event_type, 'application', NEW.application_id,
                NEW.actor_type, NEW.actor_id, NEW.from_status, NEW.to_status,
                NEW.reason, NEW.notes, COALESCE(NEW.metadata, '{}'::json), NULL,
                NEW.occurred_at
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_application_event_audit_projection
        AFTER INSERT ON application_events
        FOR EACH ROW EXECUTE FUNCTION mirror_application_event_to_audit()
    """)

    op.execute("""
        CREATE FUNCTION mirror_admin_event_to_audit()
        RETURNS trigger AS $$
        BEGIN
            INSERT INTO audit_events (
                id, event_type, resource_type, resource_id, actor_type, actor_id,
                from_status, to_status, reason, notes, details, request_id, occurred_at
            ) VALUES (
                gen_random_uuid(), NEW.action, NEW.resource_type, NEW.resource_id,
                'USER', NEW.actor_user_id, NULL, NULL, NULL, NULL,
                json_build_object('outcome', NEW.outcome, 'details', NEW.details),
                NEW.request_id, NEW.created_at
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_admin_event_audit_projection
        AFTER INSERT ON admin_audit_events
        FOR EACH ROW EXECUTE FUNCTION mirror_admin_event_to_audit()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_admin_event_audit_projection ON admin_audit_events")
    op.execute("DROP TRIGGER IF EXISTS trg_application_event_audit_projection ON application_events")
    op.execute("DROP FUNCTION IF EXISTS mirror_admin_event_to_audit()")
    op.execute("DROP FUNCTION IF EXISTS mirror_application_event_to_audit()")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_history_mutation()")
    op.drop_index("ix_audit_events_event_occurred", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_occurred", table_name="audit_events")
    op.drop_index("ix_audit_events_resource_occurred", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_column("application_events", "notes")
    op.drop_column("application_events", "reason")
