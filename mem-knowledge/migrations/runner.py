"""Explicit migration adoption with one transaction and one Knowledge-only lock."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from .schema import (
    BASELINE_REVISION,
    MEDIA_REVISION,
    OWNED_TABLES,
    VERSION_TABLE,
    schema_differences,
)

MIGRATION_LOCK_KEY = 0x4D424B4E4F574C
LOCK_TIMEOUT_MS = 5_000
STATEMENT_TIMEOUT_MS = 60_000
VERSION_SCHEMA = "public"
VERIFIED_ADOPTION_ATTRIBUTE = "knowledge_verified_adoption"


class MigrationSafetyError(RuntimeError):
    """A redacted, actionable refusal; never a request to repair schema implicitly."""


def migration_config() -> Config:
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def current_revisions(connection) -> tuple[str, ...]:
    return MigrationContext.configure(
        connection,
        opts={
            "version_table": VERSION_TABLE,
            "version_table_schema": VERSION_SCHEMA,
        },
    ).get_current_heads()


def prepare_connection(connection, *, lock: bool) -> None:
    if connection.dialect.name != "postgresql":
        raise MigrationSafetyError("Knowledge migrations require PostgreSQL")
    connection.execute(text("SET LOCAL search_path TO public"))
    connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    for name, value in (
        ("lock_timeout", LOCK_TIMEOUT_MS),
        ("statement_timeout", STATEMENT_TIMEOUT_MS),
    ):
        connection.execute(
            text("SELECT set_config(:name, :value, true)"), {"name": name, "value": f"{value}ms"}
        )
    if (
        lock
        and not connection.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": MIGRATION_LOCK_KEY},
        ).scalar_one()
    ):
        raise MigrationSafetyError(
            "Another Knowledge migration is running; retry after it completes"
        )

    if lock:
        present = OWNED_TABLES.intersection(inspect(connection).get_table_names(schema="public"))
        if present:
            # Stabilize schema against DDL that does not use our advisory lock.
            # SHARE UPDATE EXCLUSIVE remains compatible with ordinary row writes.
            names = ", ".join(f'public."{name}"' for name in sorted(present))
            connection.execute(text(f"LOCK TABLE {names} IN SHARE UPDATE EXCLUSIVE MODE"))


def inspect_schema(connection, revision: str) -> dict:
    present = sorted(
        OWNED_TABLES.intersection(inspect(connection).get_table_names(schema="public"))
    )
    differences = schema_differences(connection, revision)
    return {
        "revision": revision,
        "current_revisions": list(current_revisions(connection)),
        "present_tables": present,
        "matches": not differences,
        "differences": differences,
    }


def adopt(connection, revision: str, *, config: Config | None = None) -> dict:
    """Stamp only a fully verified schema inside the caller's locked transaction."""
    if not connection.in_transaction():
        raise MigrationSafetyError("Adoption requires an explicit transaction")
    prepare_connection(connection, lock=True)
    if revision not in {BASELINE_REVISION, MEDIA_REVISION}:
        raise MigrationSafetyError("Adoption requires an explicit known baseline or media revision")
    state = inspect_schema(connection, revision)
    if not state["matches"]:
        raise MigrationSafetyError(
            "Schema differs from the requested revision; run inspect for details"
        )
    current = tuple(state["current_revisions"])
    allowed = {(), (revision,)}
    if revision == MEDIA_REVISION:
        allowed.add((BASELINE_REVISION,))
    if current not in allowed:
        raise MigrationSafetyError(
            "Existing Knowledge revision conflicts; do not overwrite migration history"
        )
    config = config or migration_config()
    old = dict(config.attributes)
    try:
        config.attributes.update(connection=connection)
        config.attributes[VERIFIED_ADOPTION_ATTRIBUTE] = revision
        command.stamp(config, revision)
    finally:
        config.attributes.clear()
        config.attributes.update(old)
    return {
        "adopted_revision": revision,
        "schema_changed": False,
        "current_revisions": list(current_revisions(connection)),
    }


async def run_online(callback, *, read_only: bool = False):
    """Use the service's existing URL configuration, without starting any service."""
    from src.bootstrap import get_settings

    engine = create_async_engine(get_settings().database_url_async, poolclass=pool.NullPool)
    try:
        async with engine.begin() as connection:
            if read_only:
                await connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
            await connection.run_sync(lambda conn: prepare_connection(conn, lock=not read_only))
            return await connection.run_sync(callback)
    finally:
        await engine.dispose()


def main(action: str, revision: str) -> dict:
    callback = (
        (lambda connection: inspect_schema(connection, revision))
        if action == "inspect"
        else (lambda connection: adopt(connection, revision))
    )
    return asyncio.run(run_online(callback, read_only=action == "inspect"))
