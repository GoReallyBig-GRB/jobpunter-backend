"""Persist explicit AI output-validation outcomes.

Revision ID: 0040_ai_validation_result
Revises: 0039_ai_input_fingerprint
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_ai_validation_result"
down_revision = "0039_ai_input_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column(
            "validation_result",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{\"status\":\"NOT_RUN\"}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_executions", "validation_result")
