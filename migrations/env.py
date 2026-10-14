from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, inspect, text
from alembic import context

from app.config.settings import get_settings
from app.infrastructure.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # PR #55 originally used a revision ID longer than Alembic's default
        # VARCHAR(32) version_num column can store. Normalize that legacy
        # stamp before Alembic resolves the revision graph. The schema changes
        # represented by both IDs are identical.
        if inspect(connection).has_table("alembic_version"):
            with connection.begin():
                connection.execute(
                    text(
                        "UPDATE alembic_version "
                        "SET version_num = :canonical "
                        "WHERE version_num = :legacy"
                    ),
                    {
                        "canonical": "0032_candidate_fact_provenance",
                        "legacy": "0032_candidate_profile_fact_provenance",
                    },
                )
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
