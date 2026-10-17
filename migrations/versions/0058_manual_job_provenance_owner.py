"""Track submitter ownership on manual-source job provenance.

Revision ID: 0058_manual_job_provenance_owner
Revises: 0057_support_tickets
"""
from alembic import op
import sqlalchemy as sa

revision = "0058_manual_job_provenance_owner"
down_revision = "0057_support_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_provenance",
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True),
    )
    # Historical manual-ingestion run keys were scoped to the authenticated user.
    # Join against users instead of casting arbitrary run-key text to UUID.
    op.execute(
        """
        UPDATE job_provenance AS provenance
        SET submitted_by_user_id = owner.id
        FROM raw_source_records AS raw
        JOIN acquisition_runs AS run ON run.id = raw.acquisition_run_id
        JOIN sources AS source ON source.id = raw.source_id
        JOIN users AS owner ON run.run_key LIKE 'manual:' || owner.id::text || ':%'
        WHERE provenance.raw_source_record_id = raw.id
          AND source.source_type = 'MANUAL_SUBMISSION'
        """
    )
    op.create_foreign_key(
        "fk_job_provenance_submitted_by_user_id_users",
        "job_provenance",
        "users",
        ["submitted_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_job_provenance_submitted_by_user_id",
        "job_provenance",
        ["submitted_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_provenance_submitted_by_user_id", table_name="job_provenance")
    op.drop_constraint(
        "fk_job_provenance_submitted_by_user_id_users",
        "job_provenance",
        type_="foreignkey",
    )
    op.drop_column("job_provenance", "submitted_by_user_id")