"""Link structured document extraction to its governed AI execution.

Revision ID: 0043_document_ai_execution_ref
Revises: 0042_ai_output_fingerprint
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_document_ai_execution_ref"
down_revision = "0042_ai_output_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_imports",
        sa.Column("ai_execution_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_document_imports_ai_execution_id_ai_executions",
        "document_imports",
        "ai_executions",
        ["ai_execution_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_document_imports_ai_execution_id_ai_executions",
        "document_imports",
        type_="foreignkey",
    )
    op.drop_column("document_imports", "ai_execution_id")
