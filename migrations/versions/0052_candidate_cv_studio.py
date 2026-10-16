"""Add immutable candidate-owned Master CV and variant versions.

Revision ID: 0052_candidate_cv_studio
Revises: 0051_discovery_schedule_contract
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0052_candidate_cv_studio"
down_revision = "0051_discovery_schedule_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_cv_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cv_key", sa.String(length=64), nullable=False),
        sa.Column("cv_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_profile_revision", sa.Integer(), nullable=False),
        sa.Column("source_profile_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_profile_hash", sa.String(length=64), nullable=False),
        sa.Column("source_master_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidate_cv_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_profile_id", "cv_key", "version_number", name="uq_candidate_cv_version_key"),
        sa.CheckConstraint("cv_type IN ('MASTER', 'VARIANT')", name="ck_candidate_cv_version_type"),
        sa.CheckConstraint("(cv_type = 'MASTER' AND cv_key = 'master') OR (cv_type = 'VARIANT' AND cv_key <> 'master')", name="ck_candidate_cv_master_key"),
        sa.CheckConstraint("version_number >= 1", name="ck_candidate_cv_version_positive"),
        sa.CheckConstraint("source_profile_revision >= 1", name="ck_candidate_cv_profile_revision"),
        sa.CheckConstraint("cv_key ~ '^[a-z0-9][a-z0-9_-]{0,63}$'", name="ck_candidate_cv_key_format"),
    )
    op.create_index(
        "ix_candidate_cv_versions_lookup", "candidate_cv_versions",
        ["candidate_profile_id", "cv_key", "version_number"],
    )
    op.create_table(
        "candidate_cv_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cv_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidate_cv_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("cv_version_id", name="uq_candidate_cv_approval_version"),
    )
    op.create_index(
        "ix_candidate_cv_approvals_candidate", "candidate_cv_approvals",
        ["candidate_profile_id", "approved_at"],
    )
    op.execute("""
        CREATE FUNCTION prevent_candidate_cv_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'candidate CV versions and approvals are append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("candidate_cv_versions", "candidate_cv_approvals"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_candidate_cv_mutation()
        """)
        op.execute(f"""
            CREATE TRIGGER trg_{table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_candidate_cv_mutation()
        """)


def downgrade() -> None:
    for table in ("candidate_cv_approvals", "candidate_cv_versions"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_candidate_cv_mutation()")
    op.drop_index("ix_candidate_cv_approvals_candidate", table_name="candidate_cv_approvals")
    op.drop_table("candidate_cv_approvals")
    op.drop_index("ix_candidate_cv_versions_lookup", table_name="candidate_cv_versions")
    op.drop_table("candidate_cv_versions")
