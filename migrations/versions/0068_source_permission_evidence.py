"""Invalidate source approvals missing required permission evidence.

Revision ID: 0068_source_permission_evidence
Revises: 0067_source_health_states
"""
from alembic import op

revision = "0068_source_permission_evidence"
down_revision = "0067_source_health_states"
branch_labels = None
depends_on = None

_MISSING_EVIDENCE = """
    NULLIF(BTRIM(configuration->'permission_review'->>'access_method'), '') IS NULL
    OR NULLIF(BTRIM(configuration->'permission_review'->>'terms_reference'), '') IS NULL
    OR NULLIF(BTRIM(configuration->'permission_review'->>'rate_limit_policy'), '') IS NULL
    OR NULLIF(BTRIM(configuration->'permission_review'->>'rate_limit_reference'), '') IS NULL
"""


def upgrade() -> None:
    op.execute(f"""
        WITH invalid_sources AS (
            SELECT
                id,
                array_remove(ARRAY[
                    CASE WHEN NULLIF(BTRIM(configuration->'permission_review'->>'access_method'), '') IS NULL
                        THEN 'access_method' END,
                    CASE WHEN NULLIF(BTRIM(configuration->'permission_review'->>'terms_reference'), '') IS NULL
                        THEN 'terms_reference' END,
                    CASE WHEN NULLIF(BTRIM(configuration->'permission_review'->>'rate_limit_policy'), '') IS NULL
                        THEN 'rate_limit_policy' END,
                    CASE WHEN NULLIF(BTRIM(configuration->'permission_review'->>'rate_limit_reference'), '') IS NULL
                        THEN 'rate_limit_reference' END
                ], NULL) AS missing_fields
            FROM sources
            WHERE permission_review_status = 'APPROVED'
              AND ({_MISSING_EVIDENCE})
        )
        INSERT INTO audit_events (
            event_type, resource_type, resource_id, actor_type, actor_id,
            from_status, to_status, reason, details, occurred_at
        )
        SELECT
            'SOURCE_PERMISSION_REVIEW_EVIDENCE_INVALIDATED',
            'source',
            id,
            'SYSTEM',
            NULL,
            'APPROVED',
            'PENDING',
            'APPROVED_REVIEW_MISSING_REQUIRED_EVIDENCE',
            json_build_object(
                'missing_fields', missing_fields,
                'migration_revision', '0068_source_permission_evidence'
            ),
            NOW()
        FROM invalid_sources
    """)
    op.execute(f"""
        UPDATE sources
        SET permission_review_status = 'PENDING',
            permission_reviewed_at = NULL,
            operational_state = 'RESTRICTED',
            last_error_category = 'PERMISSION_REVIEW_REQUIRED',
            updated_at = NOW()
        WHERE permission_review_status = 'APPROVED'
          AND ({_MISSING_EVIDENCE})
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_events
                WHERE event_type = 'SOURCE_PERMISSION_REVIEW_EVIDENCE_INVALIDATED'
            ) THEN
                RAISE EXCEPTION
                    'Cannot restore incomplete source permission approvals during downgrade';
            END IF;
        END
        $$;
    """)
