from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config.settings import get_settings
from app.infrastructure.db.models import Base
from app.infrastructure.db.migration_compat import (
    ensure_alembic_version_column_capacity,
    normalize_legacy_alembic_stamps,
)

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Prepare existing databases before Alembic resolves revision history.
        with connection.begin():
            ensure_alembic_version_column_capacity(connection)
            normalize_legacy_alembic_stamps(connection)

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
