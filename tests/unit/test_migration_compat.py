from unittest.mock import Mock, patch

from sqlalchemy import String, create_engine, text

from app.infrastructure.db.migration_compat import (
    ALEMBIC_VERSION_COLUMN_LENGTH,
    ensure_alembic_version_column_capacity,
    normalize_legacy_alembic_stamps,
)


def test_normalizes_only_the_known_legacy_alembic_stamp():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) VALUES "
                    "('0032_candidate_profile_fact_provenance'), "
                    "('0031_previous_revision')"
                )
            )

            normalize_legacy_alembic_stamps(connection)

            versions = set(
                connection.scalars(
                    text("SELECT version_num FROM alembic_version")
                )
            )
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


def test_version_column_capacity_is_noop_for_non_postgresql():
    connection = Mock()
    connection.dialect.name = "sqlite"

    ensure_alembic_version_column_capacity(connection)

    connection.execute.assert_not_called()


def test_version_column_capacity_is_noop_without_version_table():
    connection = Mock()
    connection.dialect.name = "postgresql"
    inspector = Mock()
    inspector.has_table.return_value = False

    with patch(
        "app.infrastructure.db.migration_compat.inspect",
        return_value=inspector,
    ):
        ensure_alembic_version_column_capacity(connection)

    connection.execute.assert_not_called()


def test_version_column_capacity_widens_narrow_postgresql_column():
    connection = Mock()
    connection.dialect.name = "postgresql"
    inspector = Mock()
    inspector.has_table.return_value = True
    inspector.get_columns.return_value = [
        {"name": "version_num", "type": String(32)}
    ]

    with patch(
        "app.infrastructure.db.migration_compat.inspect",
        return_value=inspector,
    ):
        ensure_alembic_version_column_capacity(connection)

    connection.execute.assert_called_once()
    statement = str(connection.execute.call_args.args[0])
    assert "ALTER TABLE alembic_version" in statement
    assert "ALTER COLUMN version_num TYPE VARCHAR(128)" in statement


def test_version_column_capacity_preserves_sufficient_column_length():
    connection = Mock()
    connection.dialect.name = "postgresql"
    inspector = Mock()
    inspector.has_table.return_value = True
    inspector.get_columns.return_value = [
        {"name": "version_num", "type": String(ALEMBIC_VERSION_COLUMN_LENGTH)}
    ]

    with patch(
        "app.infrastructure.db.migration_compat.inspect",
        return_value=inspector,
    ):
        ensure_alembic_version_column_capacity(connection)

    connection.execute.assert_not_called()
