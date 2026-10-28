"""Synchronous PostgreSQL lifecycle for Celery worker processes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import JobsSettings

logger = logging.getLogger(__name__)


class PostgresManager:
    """Own one blocking SQLAlchemy engine per worker process.

    Celery's prefork pool forks after modules are imported, so the engine is
    created lazily on first use: children never inherit sockets opened by the
    parent. ``reset_after_fork`` additionally replaces an engine the parent did
    touch (for example during a startup probe).
    """

    def __init__(self, settings: JobsSettings) -> None:
        self._settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._lock = threading.Lock()

    @property
    def initialized(self) -> bool:
        return self._engine is not None

    def engine(self) -> Engine:
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is None:
                url = self._settings.sync_database_url
                connect_args: dict[str, object] = {}
                if url.startswith("postgresql"):
                    connect_args = {
                        "connect_timeout": self._settings.database_connect_timeout_seconds,
                        "application_name": self._settings.service_name,
                        "options": "-c timezone=UTC",
                    }
                self._engine = create_engine(
                    url,
                    pool_pre_ping=True,
                    pool_reset_on_return="rollback",
                    pool_size=self._settings.database_pool_size,
                    max_overflow=self._settings.database_max_overflow,
                    pool_timeout=self._settings.database_pool_timeout_seconds,
                    pool_recycle=self._settings.database_pool_recycle_seconds,
                    connect_args=connect_args,
                )
                self._session_factory = sessionmaker(
                    bind=self._engine,
                    autoflush=False,
                    expire_on_commit=False,
                )
            return self._engine

    def session_factory(self) -> sessionmaker[Session]:
        self.engine()
        if self._session_factory is None:
            raise RuntimeError("PostgreSQL session factory was not initialized")
        return self._session_factory

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Run one unit of work: commit on success, roll back on any error.

        The commit lives here, not in the task, so a task can never leave a
        connection checked out with an open transaction (the classic source of
        "idle in transaction" and dirty pooled connections in workers).
        """

        session = self.session_factory()()
        try:
            yield session
            session.commit()
        except BaseException:
            try:
                session.rollback()
            except Exception as exc:
                # A dead connection cannot roll back. Report it as a warning
                # and re-raise the original task error instead of masking it.
                logger.warning(
                    "Failed to roll back PostgreSQL session error=%s",
                    type(exc).__name__,
                    extra={"event": "postgres_rollback_failed"},
                )
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        with self.engine().connect() as connection:
            connection.execute(select(1))
        return True

    def pool_snapshot(self) -> dict[str, object] | None:
        """Return in-memory pooled-connection counters; never touches the network."""

        engine = self._engine
        if engine is None:
            return None
        try:
            pool = engine.pool
            return {
                "size": int(pool.size()),
                "checked_in": int(pool.checkedin()),
                "checked_out": int(pool.checkedout()),
                "capacity": (
                    self._settings.database_pool_size
                    + self._settings.database_max_overflow
                ),
            }
        except Exception as exc:
            logger.warning(
                "Failed to read PostgreSQL pool state: %s",
                type(exc).__name__,
                extra={"event": "postgres_pool_snapshot_failed"},
            )
            return None

    def reset_after_fork(self) -> None:
        """Drop a parent-owned engine without closing the parent's connections."""

        engine = self._engine
        self._engine = None
        self._session_factory = None
        if engine is not None:
            engine.dispose(close=False)

    def close(self) -> None:
        engine = self._engine
        self._engine = None
        self._session_factory = None
        if engine is not None:
            engine.dispose()
