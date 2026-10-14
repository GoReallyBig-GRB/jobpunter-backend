"""Persist immutable effective AI contract snapshots on execution records.

Revision ID: 0041_ai_contract_snapshot
Revises: 0040_ai_validation_result
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_ai_contract_snapshot"
down_revision = "0040_ai_validation_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column(
            "contract_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{\"status\":\"LEGACY_UNAVAILABLE\"}'"),
        ),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_ai_execution_contract_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.task IS DISTINCT FROM OLD.task
                OR NEW.task_policy_version IS DISTINCT FROM OLD.task_policy_version
                OR NEW.system_policy_version IS DISTINCT FROM OLD.system_policy_version
                OR NEW.input_contract_version IS DISTINCT FROM OLD.input_contract_version
                OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
                OR NEW.output_contract_version IS DISTINCT FROM OLD.output_contract_version
                OR NEW.config_fingerprint IS DISTINCT FROM OLD.config_fingerprint
                OR NEW.config_version_id IS DISTINCT FROM OLD.config_version_id
                OR NEW.contract_snapshot::text IS DISTINCT FROM OLD.contract_snapshot::text
            THEN
                RAISE EXCEPTION 'AI execution contract provenance is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_execution_contract_immutable
        BEFORE UPDATE ON ai_executions
        FOR EACH ROW EXECUTE FUNCTION prevent_ai_execution_contract_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ai_execution_contract_immutable ON ai_executions")
    op.execute("DROP FUNCTION IF EXISTS prevent_ai_execution_contract_mutation()")
    op.drop_column("ai_executions", "contract_snapshot")
