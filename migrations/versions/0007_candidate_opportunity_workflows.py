"""Add candidate evaluations, Punts and application lifecycle records.

Revision ID: 0007_opportunity_workflows
Revises: 0006_outbox_processing_lease
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_opportunity_workflows"
down_revision = "0006_outbox_processing_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    u = sa.Uuid()
    op.create_table(
        "candidate_evaluations",
        sa.Column("id", u, primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", u, sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", u, sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_role_key", sa.String(255), nullable=False),
        sa.Column("evaluation_version", sa.Integer(), nullable=False),
        sa.Column("validity_status", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("hard_blockers", sa.JSON(), nullable=False),
        sa.Column("evidence_summary", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_profile_id", "job_id", "target_role_key", "evaluation_version", name="uq_evaluation_candidate_job_role_version"),
    )
    op.create_table(
        "punts",
        sa.Column("id", u, primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", u, sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", u, sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_profile_id", "job_id", name="uq_punt_candidate_job"),
    )
    op.create_table(
        "punt_evaluations",
        sa.Column("punt_id", u, sa.ForeignKey("punts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("evaluation_id", u, sa.ForeignKey("candidate_evaluations.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "applications",
        sa.Column("id", u, primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", u, sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", u, sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("punt_id", u, sa.ForeignKey("punts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_profile_id", "job_id", name="uq_application_candidate_job"),
    )
    op.create_table(
        "application_events",
        sa.Column("id", u, primary_key=True, nullable=False),
        sa.Column("application_id", u, sa.ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", u, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("application_events")
    op.drop_table("applications")
    op.drop_table("punt_evaluations")
    op.drop_table("punts")
    op.drop_table("candidate_evaluations")
