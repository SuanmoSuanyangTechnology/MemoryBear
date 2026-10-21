"""Wiki release migrations; existing knowledge tables remain on the API chain."""

from alembic import context
from sqlalchemy import create_engine, pool

from src import models  # noqa: F401 - register runtime metadata, including FK targets
from src.bootstrap import get_settings
from src.db import KnowledgeBase
from src.migration_policy import VERSION_TABLE, include_name, include_object

settings = context.config.attributes.get("settings") or get_settings()
options = {
    "target_metadata": KnowledgeBase.metadata,
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
