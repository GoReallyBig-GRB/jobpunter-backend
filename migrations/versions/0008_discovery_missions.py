"""Persist candidate discovery schedules, missions, and source-level outcomes.

Revision ID: 0008_discovery_missions
Revises: 0007_opportunity_workflows
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_discovery_missions"
down_revision = "0007_opportunity_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    u = sa.Uuid()
    op.create_table(
        "candidate_discovery_settings",
        sa.Column("id", u, primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", u, sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cadence_minutes", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_opportunities", sa.Integer(), nullable=False),
        sa.Column("max_job_age_days", sa.Integer(), nullable=False),
        sa.Column("verification_required", sa.Boolean(), nullable=False),
        sa.Column("evaluation_depth", sa.String(32), nullable=False),
        sa.Column("source_filters", sa.JSON(), nullable=False),
        sa.Column("last_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_profile_id", name="uq_discovery_settings_candidate"),
    )
    op.create_index(
        "ix_discovery_settings_due",
        "candidate_discovery_settings",
        ["status", "next_run_at"],
    )
    op.create_table(
        "discovery_missions",
        sa.Column("id", u, primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", u, sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("settings_id", u, sa.ForeignKey("candidate_discovery_settings.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("current_wave_key", sa.String(64), nullable=True),
        sa.Column("wave_index", sa.Integer(), nullable=False),
        sa.Column("cumulative_usable_opportunities", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_profile_id", "idempotency_key", name="uq_discovery_mission_candidate_key"),
    )
    op.create_index(
        "ix_discovery_missions_status_created",
        "discovery_missions",
        ["status", "created_at"],
    )
    op.create_table(
        "discovery_mission_source_runs",
        sa.Column("id", u, primary_key=True, nullable=False),
        sa.Column("mission_id", u, sa.ForeignKey("discovery_missions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", u, sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_connector_id", u, sa.ForeignKey("source_connectors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("acquisition_run_id", u, sa.ForeignKey("acquisition_runs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("wave_key", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("run_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("acquired_count", sa.Integer(), nullable=False),
        sa.Column("persisted_count", sa.Integer(), nullable=False),
        sa.Column("canonicalized_count", sa.Integer(), nullable=False),
        sa.Column("usable_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_details", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mission_id", "source_id", "wave_key", "attempt_number", name="uq_mission_source_wave_attempt"),
        sa.UniqueConstraint("run_key", name="uq_discovery_mission_source_run_key"),
    )
    op.create_index(
        "ix_mission_source_runs_status",
        "discovery_mission_source_runs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_mission_source_runs_status", table_name="discovery_mission_source_runs")
    op.drop_table("discovery_mission_source_runs")
    op.drop_index("ix_discovery_missions_status_created", table_name="discovery_missions")
    op.drop_table("discovery_missions")
    op.drop_index("ix_discovery_settings_due", table_name="candidate_discovery_settings")
    op.drop_table("candidate_discovery_settings")
