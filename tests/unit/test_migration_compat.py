from sqlalchemy import create_engine, text

from app.infrastructure.db.migration_compat import normalize_legacy_alembic_stamps


def test_normalizes_only_the_known_legacy_alembic_stamp():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO alembic_version (version_num) VALUES "
                "('0032_candidate_profile_fact_provenance'), ('0031_previous_revision')"
            ))

            normalize_legacy_alembic_stamps(connection)

            versions = set(connection.scalars(text("SELECT version_num FROM alembic_version")))
            assert versions == {
                "0032_candidate_fact_provenance",
                "0031_previous_revision",
            }
    finally:
        engine.dispose()


def test_legacy_stamp_normalization_is_noop_without_alembic_version_table():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            normalize_legacy_alembic_stamps(connection)
    finally:
        engine.dispose()
