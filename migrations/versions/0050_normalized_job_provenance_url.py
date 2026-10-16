"""Add normalized source URL identity for safe vacancy convergence.

Revision ID: 0050_normalized_job_provenance_url
Revises: 0049_job_requirement_dimension
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from alembic import op
import sqlalchemy as sa

revision = "0050_normalized_job_provenance_url"
down_revision = "0049_job_requirement_dimension"
branch_labels = None
depends_on = None

_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "refid", "trackingid", "trk"}


def _normalize_source_url(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.netloc or not host:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    host = host.lower()
    authority = f"[{host}]" if ":" in host else host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        authority = f"{authority}:{port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ))
    return urlunsplit((scheme, authority, path, query, ""))


def upgrade() -> None:
    op.add_column("job_provenance", sa.Column("normalized_source_url", sa.Text(), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, source_url FROM job_provenance WHERE source_url IS NOT NULL"
    )).fetchall()
    for row in rows:
        normalized = _normalize_source_url(row.source_url)
        if normalized is not None:
            bind.execute(
                sa.text(
                    "UPDATE job_provenance SET normalized_source_url = :normalized "
                    "WHERE id = :row_id"
                ),
                {"normalized": normalized, "row_id": row.id},
            )
    op.create_index(
        "ix_job_provenance_normalized_source_url",
        "job_provenance",
        ["normalized_source_url"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_provenance_normalized_source_url", table_name="job_provenance")
    op.drop_column("job_provenance", "normalized_source_url")
