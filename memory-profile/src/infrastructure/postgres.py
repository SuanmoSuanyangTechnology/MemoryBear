"""Loop-safe asynchronous PostgreSQL ORM lifecycle."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import ProfileSettings

logger = logging.getLogger(__name__)


def _driver_connection(dbapi_connection: Any) -> Any | None:
    if dbapi_connection is None:
        return None
    driver = getattr(dbapi_connection, "driver_connection", None)
    if driver is not None:
        return driver
    return getattr(dbapi_connection, "_connection", None)


def _has_active_transaction(dbapi_connection: Any) -> bool:
    driver = _driver_connection(dbapi_connection)
    if driver is None:
        return False
    checker = getattr(driver, "is_in_transaction", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        logger.exception(
            "Failed to inspect PostgreSQL connection transaction state conn_id=%s",
            id(driver),
            extra={"event": "postgres_connection_inspection_failed"},
        )
        return True


def _reject_dirty_checkin(dbapi_connection: Any, connection_record: Any) -> None:
    """Hard-invalidate a connection that rollback-on-return failed to clean."""

    if not _has_active_transaction(dbapi_connection):
        return
    driver = _driver_connection(dbapi_connection)
    error = RuntimeError("dirty asyncpg connection on checkin")
    logger.error(
        "Invalidating dirty PostgreSQL connection on checkin conn_id=%s",
        id(driver),
        extra={"event": "postgres_dirty_connection_checkin"},
    )
    connection_record.invalidate(error)


def _reject_dirty_checkout(
    dbapi_connection: Any,
    connection_record: Any,
    connection_proxy: Any,
) -> None:
    """Reject a dirty pooled connection and let SQLAlchemy retry with a new one."""

    del connection_proxy
    if not _has_active_transaction(dbapi_connection):
        return
    driver = _driver_connection(dbapi_connection)
    error = RuntimeError("dirty asyncpg connection on checkout")
    logger.error(
        "Rejecting dirty PostgreSQL connection on checkout conn_id=%s",
        id(driver),
        extra={"event": "postgres_dirty_connection_checkout"},
    )
    connection_record.invalidate(error)
    raise DisconnectionError(str(error)) from error


def _install_pool_guards(engine: AsyncEngine) -> None:
    event.listen(engine.sync_engine, "checkin", _reject_dirty_checkin)
    event.listen(engine.sync_engine, "checkout", _reject_dirty_checkout)


class PostgresManager:
    """Lazily own one async engine and short-lived read-only ORM sessions."""

    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._engine is not None

    async def engine(self) -> AsyncEngine:
        current_loop = asyncio.get_running_loop()
        if self._engine is not None and self._loop is not current_loop:
            raise RuntimeError("PostgreSQL engine cannot be shared across event loops")
        async with self._lock:
            if self._engine is None:
                engine = create_async_engine(
                    self._settings.async_database_url,
                    pool_pre_ping=True,
                    pool_reset_on_return="rollback",
                    pool_size=self._settings.database_pool_size,
                    max_overflow=self._settings.database_max_overflow,
                    pool_timeout=self._settings.database_pool_timeout_seconds,
                    pool_recycle=self._settings.database_pool_recycle_seconds,
                    connect_args={
                        "server_settings": {
                            "timezone": "UTC",
                            "default_transaction_read_only": "on",
                        }
                    },
                )
                _install_pool_guards(engine)
                self._engine = engine
                self._session_factory = async_sessionmaker(
                    bind=engine,
                    class_=AsyncSession,
                    autoflush=False,
                    expire_on_commit=False,
                )
                self._loop = current_loop
            return self._engine

    async def session_factory(self) -> async_sessionmaker[AsyncSession]:
        await self.engine()
        if self._session_factory is None:
            raise RuntimeError("PostgreSQL session factory was not initialized")
        return self._session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        factory = await self.session_factory()
        async with factory() as session:
            try:
                yield session
            finally:
                if session.in_transaction():
                    await session.rollback()

    async def ping(self) -> bool:
        async with self.session() as session:
            await session.scalar(select(1))
        return True

    async def aclose(self) -> None:
        engine = self._engine
        self._engine = None
        self._session_factory = None
        self._loop = None
        if engine is not None:
            await engine.dispose()
