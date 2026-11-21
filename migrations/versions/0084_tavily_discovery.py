"""Add the search credit ledger and discovered ATS boards.

Revision ID: 0084_tavily_discovery
Revises: 0083_candidate_pool_entries
"""

import sqlalchemy as sa
from alembic import op

revision = "0084_tavily_discovery"
down_revision = "0083_candidate_pool_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_credit_ledger",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("query_key", sa.String(64), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("credits_used", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("new_boards_count", sa.Integer(), nullable=False),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("credits_used >= 0", name="ck_search_credit_ledger_credits"),
        sa.CheckConstraint(
            "outcome IN ('SUCCEEDED', 'FAILED', 'RATE_LIMITED', 'LIMIT_EXCEEDED', 'INVALID_KEY')",
            name="ck_search_credit_ledger_outcome",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_credit_ledger_provider_created", "search_credit_ledger", ["provider", "created_at"],
    )
    op.create_table(
        "discovered_ats_boards",
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("token", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reject_reason", sa.String(64), nullable=True),
        sa.Column("open_jobs_seen", sa.Integer(), nullable=True),
        sa.Column("found_via_url", sa.String(2048), nullable=False),
        sa.Column("found_via_query_key", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PROBE_OK', 'REJECTED', 'REGISTERED')", name="ck_discovered_ats_board_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "token", name="uq_discovered_ats_board_provider_token"),
    )
    op.create_index("ix_discovered_ats_boards_status", "discovered_ats_boards", ["status", "first_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_discovered_ats_boards_status", table_name="discovered_ats_boards")
    op.drop_table("discovered_ats_boards")
    op.drop_index("ix_search_credit_ledger_provider_created", table_name="search_credit_ledger")
    op.drop_table("search_credit_ledger")
