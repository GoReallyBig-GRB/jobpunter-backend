"""Compatibility normalization for known legacy Alembic version stamps."""
from sqlalchemy import inspect, text


LEGACY_ALEMBIC_STAMPS = {
    "0032_candidate_profile_fact_provenance": "0032_candidate_fact_provenance",
}


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
            {"canonical": canonical, "legacy": legacy},
        )
