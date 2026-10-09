"""Persist the input versions and snapshot used for each evaluation.

Revision ID: 0014_evaluation_input_provenance
Revises: 0013_candidate_preferences
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_evaluation_input_provenance"
down_revision = "0013_candidate_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidate_evaluations", sa.Column("candidate_profile_revision", sa.BigInteger(), nullable=False, server_default="1"))
    op.add_column("candidate_evaluations", sa.Column("preferences_revision", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("candidate_evaluations", sa.Column("job_description_version_id", sa.Uuid(), nullable=True))
    op.add_column("candidate_evaluations", sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_foreign_key(
        "fk_candidate_evaluations_job_description_version",
        "candidate_evaluations", "job_description_versions",
        ["job_description_version_id"], ["id"], ondelete="RESTRICT",
    )
    op.alter_column("candidate_evaluations", "candidate_profile_revision", server_default=None)
    op.alter_column("candidate_evaluations", "preferences_revision", server_default=None)
    op.alter_column("candidate_evaluations", "input_snapshot", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_candidate_evaluations_job_description_version", "candidate_evaluations", type_="foreignkey")
    op.drop_column("candidate_evaluations", "input_snapshot")
    op.drop_column("candidate_evaluations", "job_description_version_id")
    op.drop_column("candidate_evaluations", "preferences_revision")
    op.drop_column("candidate_evaluations", "candidate_profile_revision")
