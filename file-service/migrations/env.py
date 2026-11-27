"""File-service release migrations; the file tables are shared with the monolith during coexistence."""

from alembic import context
from sqlalchemy import create_engine, pool

from src import models  # noqa: F401 - register metadata
from src.config import settings
from src.migration_policy import VERSION_TABLE, include_name, include_object
from src.models import ServiceBase

options = {
    "target_metadata": ServiceBase.metadata,
    "version_table": VERSION_TABLE,
    "include_name": include_name,
    "include_object": include_object,
    "compare_type": True,
    "compare_server_default": True,
    "transaction_per_migration": True,
    "dont_mutate": bool(context.config.attributes.get("read_only", False)),
}


def run_migrations(connection):
    context.configure(connection=connection, **options)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=settings.database_url_sync, literal_binds=True, **options)
    with context.begin_transaction():
        context.run_migrations()
elif (connection := context.config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    engine = create_engine(settings.database_url_sync, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
