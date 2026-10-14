"""Enforce immutable identity and payload for governance configuration versions.

Revision ID: 0036_immutable_governance_config
Revises: 0035_ai_execution_fallback
"""
from alembic import op

revision = "0036_immutable_governance_config"
down_revision = "0035_ai_execution_fallback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION reject_governance_config_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION 'Governance configuration history cannot be truncated'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Governance configuration versions cannot be deleted'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.config_key IS DISTINCT FROM OLD.config_key
                OR NEW.version IS DISTINCT FROM OLD.version
                OR NEW.payload::text IS DISTINCT FROM OLD.payload::text
                OR NEW.payload_fingerprint IS DISTINCT FROM OLD.payload_fingerprint
                OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id THEN
                RAISE EXCEPTION 'Governance configuration version identity and payload are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_governance_config_immutable
        BEFORE UPDATE OR DELETE ON governance_config_versions
        FOR EACH ROW EXECUTE FUNCTION reject_governance_config_mutation();
    """)
    op.execute("""
        CREATE TRIGGER trg_governance_config_no_truncate
        BEFORE TRUNCATE ON governance_config_versions
        FOR EACH STATEMENT EXECUTE FUNCTION reject_governance_config_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_governance_config_no_truncate ON governance_config_versions")
    op.execute("DROP TRIGGER IF EXISTS trg_governance_config_immutable ON governance_config_versions")
    op.execute("DROP FUNCTION IF EXISTS reject_governance_config_mutation()")
