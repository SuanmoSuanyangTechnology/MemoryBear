"""Run the committed knowledge migration chain before the API accepts traffic."""

from __future__ import annotations

import asyncio
import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .bootstrap import resolve_bootstrap_paths
from .migration_policy import VERSION_TABLE
from .runtime import ProcessRuntime

logger = logging.getLogger(__name__)
_MIGRATION_LOCK_KEY = f"mem-knowledge:{VERSION_TABLE}"


def _upgrade_database(runtime: ProcessRuntime) -> None:
    service_root = resolve_bootstrap_paths().service_root
    config = Config(str(service_root / "alembic.ini"))
    config.set_main_option("script_location", str(service_root / "migrations"))
    config.attributes["settings"] = runtime.settings
    # The lock, DDL and version update share one transaction and one connection.
    with runtime.database.sync_connection() as connection, connection.begin():
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": _MIGRATION_LOCK_KEY},
        )
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


async def upgrade_database_on_startup(runtime: ProcessRuntime) -> None:
    settings = runtime.settings
    if settings.kb_process_role != "api" or not settings.db_auto_upgrade:
        logger.info("Automatic knowledge database upgrade disabled for role=%s",
                    settings.kb_process_role)
        return
    logger.info("Automatic knowledge database upgrade started version_table=%s", VERSION_TABLE)
    try:
        await asyncio.to_thread(_upgrade_database, runtime)
    except Exception as exc:
        logger.error(
            "Automatic knowledge database upgrade failed error_type=%s", type(exc).__name__
        )
        raise
    logger.info("Automatic knowledge database upgrade completed version_table=%s", VERSION_TABLE)
