"""Align new evidence records with the evidence state machine.

Revision ID: 0012_evidence_draft_default
Revises: 0011_req_review_events
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_evidence_draft_default"
down_revision = "0011_req_review_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows are preserved. Only the database default for future inserts changes.
    op.alter_column(
        "evidence",
        "status",
        existing_type=sa.String(length=32),
        server_default="DRAFT",
    )


def downgrade() -> None:
    op.alter_column(
        "evidence",
        "status",
        existing_type=sa.String(length=32),
        server_default="PENDING_REVIEW",
    )
