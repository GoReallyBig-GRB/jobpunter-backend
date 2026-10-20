"""Compatibility helpers for Alembic version stamps and revision history."""

from sqlalchemy import inspect, text


LEGACY_ALEMBIC_STAMPS = {
    "0032_candidate_profile_fact_provenance": "0032_candidate_fact_provenance",
}

ALEMBIC_VERSION_COLUMN_LENGTH = 128


def ensure_alembic_version_column_capacity(connection) -> None:
    """Widen PostgreSQL's existing Alembic version column when necessary.

    Fresh databases are handled by the root migration before its revision is
    stamped. This function handles databases whose version table already exists.
    """
    if connection.dialect.name != "postgresql":
        return

    inspector = inspect(connection)
    if not inspector.has_table("alembic_version"):
        return

    columns = {
        column["name"]: column
        for column in inspector.get_columns("alembic_version")
    }
    version_column = columns.get("version_num")
    if version_column is None:
        return

    current_length = getattr(version_column["type"], "length", None)
    # None represents an unbounded column; do not narrow it.
    if current_length is None or current_length >= ALEMBIC_VERSION_COLUMN_LENGTH:
        return

    connection.execute(
        text(
            "ALTER TABLE alembic_version "
            "ALTER COLUMN version_num TYPE VARCHAR(128)"
        )
    )


def normalize_legacy_alembic_stamps(connection) -> None:
    """Rewrite known legacy aliases before Alembic resolves its revision graph."""
    if not inspect(connection).has_table("alembic_version"):
        return

    for legacy, canonical in LEGACY_ALEMBIC_STAMPS.items():
        connection.execute(
            text(
                "UPDATE alembic_version "
                "SET version_num = :canonical "
                "WHERE version_num = :legacy"
            ),
            {"legacy": legacy, "canonical": canonical},
        )
