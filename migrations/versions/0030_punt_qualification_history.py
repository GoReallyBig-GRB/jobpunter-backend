"""Persist immutable Punt qualification history.

Revision ID: 0030_punt_qualification_history
Revises: 0029_acquisition_run_lease
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0030_punt_qualification_history"
down_revision = "0029_acquisition_run_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "punt_qualification_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("punt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_version", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("hard_blockers", sa.JSON(), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["punt_id"], ["punts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_profile_id"], ["candidate_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evaluation_id"], ["candidate_evaluations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "punt_id", "evaluation_id",
            name="uq_punt_qualification_history_punt_evaluation",
        ),
    )
    op.create_index(
        "ix_punt_qualification_history_candidate_qualified_at",
        "punt_qualification_history",
        ["candidate_profile_id", "qualified_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_punt_qualification_history_candidate_qualified_at",
        table_name="punt_qualification_history",
    )
    op.drop_table("punt_qualification_history")
