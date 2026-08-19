"""Knowledge-owned SQLAlchemy engines and short session contexts."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import KnowledgeSettings


class KnowledgeBase(DeclarativeBase):
    """Base for Knowledge-owned tables."""


class DatabaseManager:
    """Lazily own sync and async database engines for one process."""

    def __init__(self, settings: KnowledgeSettings):
        self._settings = settings
        self._sync_engine: Engine | None = None
        self._async_engine: AsyncEngine | None = None
        self._sync_session_factory: sessionmaker[Session] | None = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._lock = threading.RLock()

    @property
    def has_sync_engine(self) -> bool:
        return self._sync_engine is not None

    @property
    def has_async_engine(self) -> bool:
        return self._async_engine is not None

    def _get_sync_engine(self) -> Engine:
        with self._lock:
            if self._sync_engine is None:
                self._sync_engine = create_engine(
                    self._settings.database_url_sync,
                    pool_size=self._settings.db_pool_size,
                    max_overflow=self._settings.db_max_overflow,
                    pool_pre_ping=self._settings.db_pool_pre_ping,
                    pool_recycle=self._settings.db_pool_recycle,
                    pool_timeout=self._settings.db_pool_timeout,
                    connect_args={
                        "options": (
                            "-c timezone=UTC "
                            f"-c statement_timeout={self._settings.db_statement_timeout_ms}"
                        )
                    },
                )
            return self._sync_engine

    def _get_async_engine(self) -> AsyncEngine:
        with self._lock:
            if self._async_engine is None:
                self._async_engine = create_async_engine(
                    self._settings.database_url_async,
                    pool_size=self._settings.db_pool_size,
                    max_overflow=self._settings.db_max_overflow,
                    pool_pre_ping=self._settings.db_pool_pre_ping,
                    pool_recycle=self._settings.db_pool_recycle,
                    pool_timeout=self._settings.db_pool_timeout,
                    connect_args={
                        "server_settings": {
                            "timezone": "UTC",
                            "statement_timeout": str(
                                self._settings.db_statement_timeout_ms
                            ),
                        }
                    },
                )
            return self._async_engine

    def _get_sync_session_factory(self) -> sessionmaker[Session]:
        with self._lock:
            if self._sync_session_factory is None:
                self._sync_session_factory = sessionmaker(
                    bind=self._get_sync_engine(),
                    autoflush=False,
                    expire_on_commit=False,
                )
            return self._sync_session_factory

    def _get_async_session_factory(self) -> async_sessionmaker[AsyncSession]:
        with self._lock:
            if self._async_session_factory is None:
                self._async_session_factory = async_sessionmaker(
                    bind=self._get_async_engine(),
                    class_=AsyncSession,
                    autoflush=False,
                    expire_on_commit=False,
                )
            return self._async_session_factory

    @contextmanager
    def sync_session(self) -> Iterator[Session]:
        session = self._get_sync_session_factory()()
        try:
            yield session
        finally:
            if session.in_transaction():
                session.rollback()
            session.close()

    @asynccontextmanager
    async def async_session(self) -> AsyncIterator[AsyncSession]:
        async with self._get_async_session_factory()() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                if session.in_transaction():
                    await session.rollback()

    async def ping(self) -> bool:
        async with self._get_async_engine().connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            return result.scalar_one() == 1

    def reset_after_fork(self) -> None:
        """Drop inherited pools without touching parent-owned sockets."""

        with self._lock:
            if self._sync_engine is not None:
                self._sync_engine.dispose(close=False)
            if self._async_engine is not None:
                self._async_engine.sync_engine.dispose(close=False)
            self._sync_engine = None
            self._async_engine = None
            self._sync_session_factory = None
            self._async_session_factory = None

    async def aclose(self) -> None:
        with self._lock:
            async_engine = self._async_engine
            sync_engine = self._sync_engine
            self._async_engine = None
            self._sync_engine = None
            self._async_session_factory = None
            self._sync_session_factory = None
        if async_engine is not None:
            await async_engine.dispose()
        if sync_engine is not None:
            await asyncio.to_thread(sync_engine.dispose)
