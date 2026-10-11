"""Persist canonical payload fingerprints for governance config versions.

Revision ID: 0026_gov_config_fingerprint
Revises: 0025_ai_system_execution_actor
"""
import hashlib
import json

from alembic import op
import sqlalchemy as sa

revision = "0026_gov_config_fingerprint"
down_revision = "0025_ai_system_execution_actor"
branch_labels = None
depends_on = None


def _fingerprint(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("governance_config_versions", sa.Column("payload_fingerprint", sa.String(length=64), nullable=True))
    bind = op.get_bind()
    table = sa.table(
        "governance_config_versions", sa.column("id", sa.Uuid()),
        sa.column("payload", sa.JSON()), sa.column("payload_fingerprint", sa.String(length=64)),
    )
    rows = bind.execute(sa.select(table.c.id, table.c.payload)).mappings().all()
    for row in rows:
        bind.execute(table.update().where(table.c.id == row["id"]).values(payload_fingerprint=_fingerprint(row["payload"])))
    op.alter_column("governance_config_versions", "payload_fingerprint", existing_type=sa.String(length=64), nullable=False)


def downgrade() -> None:
    op.drop_column("governance_config_versions", "payload_fingerprint")
