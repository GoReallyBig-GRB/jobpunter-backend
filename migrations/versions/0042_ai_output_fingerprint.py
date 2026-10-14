"""Add immutable privacy-safe AI output fingerprints.

Revision ID: 0042_ai_output_fingerprint
Revises: 0041_ai_contract_snapshot
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_ai_output_fingerprint"
down_revision = "0041_ai_contract_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column("output_fingerprint", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_ai_execution_output_fingerprint_mutation() RETURNS trigger AS $$
        BEGIN
            IF OLD.output_fingerprint IS NOT NULL
                AND NEW.output_fingerprint IS DISTINCT FROM OLD.output_fingerprint
            THEN
                RAISE EXCEPTION 'AI execution output fingerprint is immutable once recorded';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_execution_output_fingerprint_immutable
        BEFORE UPDATE ON ai_executions
        FOR EACH ROW EXECUTE FUNCTION prevent_ai_execution_output_fingerprint_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ai_execution_output_fingerprint_immutable ON ai_executions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_ai_execution_output_fingerprint_mutation()")
    op.drop_column("ai_executions", "output_fingerprint")
