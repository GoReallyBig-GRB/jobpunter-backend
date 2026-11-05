"""Add dynamic source intelligence, dispatch outcomes, and coverage observations.

Revision ID: 0082_resilient_discovery
Revises: 0081_run_budgets_snapshots
"""

import sqlalchemy as sa
from alembic import op

revision = "0082_resilient_discovery"
down_revision = "0081_run_budgets_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_forbidden_target_connector_scope", "forbidden_target_rules", type_="check"
    )
    op.create_check_constraint(
        "ck_forbidden_target_connector_scope",
        "forbidden_target_rules",
        "connector_scope IN ('ALL', 'SCRAPY', 'PLAYWRIGHT', 'PUBLIC_HTTP', "
        "'JSON_API', 'RSS_ATOM', 'JOBSPY', 'GREENHOUSE_JOB_BOARD')",
    )
    op.create_table(
        "source_intelligence_profiles",
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("market_coverage", sa.JSON(), nullable=False),
        sa.Column("permission_evidence", sa.JSON(), nullable=False),
        sa.Column("health_evidence", sa.JSON(), nullable=False),
        sa.Column("yield_evidence", sa.JSON(), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('REVIEW_REQUIRED', 'APPROVED', 'REJECTED', 'PAUSED')",
            name="ck_source_intelligence_status",
        ),
        sa.CheckConstraint(
            "status <> 'APPROVED' OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_source_intelligence_approval_actor_time",
        ),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", name="uq_source_intelligence_domain"),
    )
    op.create_table(
        "source_intelligence_observations",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("observation_type", sa.String(24), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "observation_type IN ('CAPABILITY', 'MARKET', 'PERMISSION', 'HEALTH', 'YIELD')",
            name="ck_source_intelligence_observation_type",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["source_intelligence_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "source_discovery_candidates",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("normalized_url", sa.String(2048), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("source_domain", sa.String(253), nullable=False),
        sa.Column("market_scope", sa.String(16), nullable=False),
        sa.Column("nigeria_eligible", sa.Boolean(), nullable=True),
        sa.Column("source_kind", sa.String(40), nullable=False),
        sa.Column("recommended_routes", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_profile_fingerprint", sa.String(64), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(64), nullable=True),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('REVIEW_REQUIRED', 'APPROVED', 'PROCESSING', 'REJECTED', "
            "'PAUSED', 'FAILED', 'ACQUIRED')",
            name="ck_source_discovery_candidate_status",
        ),
        sa.CheckConstraint(
            "market_scope IN ('LOCAL', 'INTERNATIONAL')",
            name="ck_source_discovery_candidate_market_scope",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_source_discovery_candidate_confidence",
        ),
        sa.CheckConstraint(
            "status NOT IN ('APPROVED', 'PROCESSING', 'ACQUIRED') OR "
            "(approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL "
            "AND approved_profile_fingerprint IS NOT NULL)",
            name="ck_source_discovery_candidate_approval_actor_time",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_source_discovery_candidate_attempt_count"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["source_intelligence_profiles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_url", name="uq_source_discovery_candidate_url"),
    )
    op.create_table(
        "acquisition_dispatch_attempts",
        sa.Column("attempt_key", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=True),
        sa.Column("url_sha256", sa.String(64), nullable=False),
        sa.Column("route", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("acquired_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'PARTIAL', 'RESTRICTED', 'UNAVAILABLE', 'FAILED')",
            name="ck_acquisition_dispatch_attempt_status",
        ),
        sa.CheckConstraint(
            "route IN ('JSON_API', 'RSS_ATOM', 'PUBLIC_HTTP', 'SCRAPY', 'PLAYWRIGHT')",
            name="ck_acquisition_dispatch_attempt_route",
        ),
        sa.CheckConstraint("acquired_count >= 0", name="ck_acquisition_dispatch_acquired_count"),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["source_intelligence_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_key", name="uq_acquisition_dispatch_attempt_key"),
    )
    op.create_table(
        "discovery_coverage_observations",
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("query_fingerprint", sa.String(64), nullable=False),
        sa.Column("local_source_count", sa.Integer(), nullable=False),
        sa.Column("international_source_count", sa.Integer(), nullable=False),
        sa.Column("eligible_url_count", sa.Integer(), nullable=False),
        sa.Column("total_url_count", sa.Integer(), nullable=False),
        sa.Column("coverage_ratio", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "local_source_count >= 0 AND international_source_count >= 0 "
            "AND eligible_url_count >= 0 AND total_url_count >= 0",
            name="ck_discovery_coverage_nonnegative_counts",
        ),
        sa.CheckConstraint(
            "eligible_url_count <= total_url_count",
            name="ck_discovery_coverage_eligible_total",
        ),
        sa.CheckConstraint(
            "coverage_ratio >= 0 AND coverage_ratio <= 1",
            name="ck_discovery_coverage_ratio",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("source_discovery_candidates")
    op.drop_table("discovery_coverage_observations")
    op.drop_table("acquisition_dispatch_attempts")
    op.drop_table("source_intelligence_observations")
    op.drop_table("source_intelligence_profiles")
    op.drop_constraint(
        "ck_forbidden_target_connector_scope", "forbidden_target_rules", type_="check"
    )
    op.execute(
        "UPDATE forbidden_target_rules SET connector_scope = 'ALL' "
        "WHERE connector_scope = 'PUBLIC_HTTP'"
    )
    op.create_check_constraint(
        "ck_forbidden_target_connector_scope",
        "forbidden_target_rules",
        "connector_scope IN ('ALL', 'SCRAPY', 'PLAYWRIGHT', 'JSON_API', "
        "'RSS_ATOM', 'JOBSPY', 'GREENHOUSE_JOB_BOARD')",
    )
