"""Wiki release migrations; existing knowledge tables remain on the API chain."""

from alembic import context
from sqlalchemy import create_engine, pool

from src import models  # noqa: F401 - register runtime metadata, including FK targets
from src.bootstrap import get_settings
from src.db import KnowledgeBase
from src.migration_policy import VERSION_TABLE, include_name, include_object

settings = get_settings()
options = {
    "target_metadata": KnowledgeBase.metadata,
    "version_table": VERSION_TABLE,
    "include_name": include_name,
    "include_object": include_object,
    "compare_type": True,
    "compare_server_default": True,
    "transaction_per_migration": True,
}

if context.is_offline_mode():
    context.configure(url=settings.database_url_sync, literal_binds=True, **options)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings.database_url_sync, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, **options)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
