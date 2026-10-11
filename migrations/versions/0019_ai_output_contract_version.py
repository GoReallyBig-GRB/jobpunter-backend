"""Persist version of the server-owned AI output contract used for execution.

Revision ID: 0019_ai_output_contract_version
Revises: 0018_package_claim_validation
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_ai_output_contract_version"
down_revision = "0018_package_claim_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column(
            "output_contract_version",
            sa.String(40),
            nullable=False,
            server_default="v1",
        ),
    )
    op.alter_column("ai_executions", "output_contract_version", server_default=None)


def downgrade() -> None:
    op.drop_column("ai_executions", "output_contract_version")
