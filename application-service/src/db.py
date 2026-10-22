"""application-service-owned SQLAlchemy engines and short session contexts."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import traceback
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from .config import AppServiceSettings
from .trace import get_trace_id

logger = logging.getLogger(__name__)

# Strong references to background close-out tasks so the GC cannot collect a
# task mid-flight.
_shielded_tasks: set[asyncio.Task] = set()


async def _run_shielded(coro, what: str) -> None:
    """Run a coroutine in a separate task that survives caller cancellation.

    Cancellation from Starlette/anyio cancel scopes is level-triggered: every
    await in a cancelled task receives CancelledError again, so awaiting
    ``session.close()`` directly can be interrupted in the middle of the
    rollback/connection return and leave an asyncpg connection inside a
    transaction. A separate task is outside that cancel scope; shield keeps
    the caller's CancelledError propagating while the inner task finishes.
    """

    task = asyncio.ensure_future(coro)
    _shielded_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _shielded_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.warning("%s failed", what, exc_info=t.exception())

    task.add_done_callback(_done)
    await asyncio.shield(task)


class CancelSafeAsyncSession(AsyncSession):
    """AsyncSession whose ``close()`` cannot be interrupted by cancellation.

    Only ``close()`` is overridden: it rolls back any open transaction and
    returns the connection to the pool. ``rollback()`` keeps its original
    semantics.
    """

    async def close(self) -> None:
        await _run_shielded(super().close(), "AsyncSession.close")


def _driver_conn(dbapi_connection):
    return (
        getattr(dbapi_connection, "driver_connection", None)
        or getattr(dbapi_connection, "_connection", None)
    )


def _asyncpg_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
    driver = _driver_conn(dbapi_connection)
    if driver is None:
        return

    if getattr(driver, "is_in_transaction", lambda: False)():
        logger.error(
            "[DB_POOL_DIRTY_CHECKOUT] asyncpg connection is already in transaction "
            "conn_id=%s previous_trace_id=%s previous_checkout_stack=%s",
            id(driver),
            connection_record.info.get("checkout_trace_id"),
            connection_record.info.get("checkout_stack"),
        )
        connection_record.invalidate(
            Exception("dirty asyncpg connection on checkout")
        )
        raise DisconnectionError("dirty asyncpg connection on checkout")

    connection_record.info["checkout_trace_id"] = get_trace_id()
    connection_record.info["checkout_stack"] = "".join(
        traceback.format_stack(limit=20)
    )


def _asyncpg_checkin(dbapi_connection, connection_record) -> None:
    if dbapi_connection is None:
        return

    driver = _driver_conn(dbapi_connection)
    if driver is None:
        return

    if not getattr(driver, "is_in_transaction", lambda: False)():
        return

    # Soft invalidation: the slot is rebuilt on the next checkout in a normal
    # context. The connection cannot be terminated synchronously here because
    # checkin may be running inside a cancelled context.
    connection_record.invalidate(
        Exception("dirty asyncpg connection on checkin"), soft=True
    )
    logger.error(
        "[DB_POOL_DIRTY_CHECKIN] returning dirty asyncpg connection to pool "
        "conn_id=%s checkout_trace_id=%s checkout_stack=%s checkin_stack=%s",
        id(driver),
        connection_record.info.get("checkout_trace_id"),
        connection_record.info.get("checkout_stack"),
        "".join(traceback.format_stack(limit=20)),
    )


class DatabaseManager:
    """Lazily own sync and async database engines for one process."""

    def __init__(self, settings: AppServiceSettings):
        self._settings = settings
        self._sync_engine: Engine | None = None
        self._async_engine: AsyncEngine | None = None
        self._sync_session_factory: sessionmaker[Session] | None = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._lock = threading.RLock()

    def _get_sync_engine(self) -> Engine:
        with self._lock:
            if self._sync_engine is None:
                self._sync_engine = create_engine(
                    self._settings.database_url_sync,
                    pool_size=self._settings.application_service_db_pool_size,
                    max_overflow=self._settings.application_service_db_max_overflow,
                    pool_pre_ping=self._settings.application_service_db_pool_pre_ping,
                    pool_recycle=self._settings.application_service_db_pool_recycle,
                    pool_timeout=self._settings.application_service_db_pool_timeout,
                    connect_args={
                        "options": (
                            "-c timezone=UTC "
                            "-c statement_timeout="
                            f"{self._settings.application_service_db_statement_timeout_ms}"
                        )
                    },
                )
            return self._sync_engine

    def _get_async_engine(self) -> AsyncEngine:
        with self._lock:
            if self._async_engine is None:
                self._async_engine = create_async_engine(
                    self._settings.database_url_async,
                    pool_size=self._settings.application_service_db_pool_size,
                    max_overflow=self._settings.application_service_db_max_overflow,
                    pool_pre_ping=self._settings.application_service_db_pool_pre_ping,
                    pool_recycle=self._settings.application_service_db_pool_recycle,
                    pool_timeout=self._settings.application_service_db_pool_timeout,
                    connect_args={
                        "server_settings": {
                            "timezone": "UTC",
                            "statement_timeout": str(
                                self._settings.application_service_db_statement_timeout_ms
                            ),
                        }
                    },
                )
                event.listen(
                    self._async_engine.sync_engine, "checkout", _asyncpg_checkout
                )
                event.listen(
                    self._async_engine.sync_engine, "checkin", _asyncpg_checkin
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
                    class_=CancelSafeAsyncSession,
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

    def async_pool(self):
        """The async engine's connection pool (created on first use)."""
        return self._get_async_engine().pool

    async def ping(self) -> bool:
        async with self._get_async_engine().connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            return result.scalar_one() == 1

    async def aclose_async(self) -> None:
        with self._lock:
            async_engine = self._async_engine
            self._async_engine = None
            self._async_session_factory = None
        if async_engine is not None:
            await async_engine.dispose()

    def close_sync(self) -> None:
        with self._lock:
            sync_engine = self._sync_engine
            self._sync_engine = None
            self._sync_session_factory = None
        if sync_engine is not None:
            sync_engine.dispose()


# ---------------------------------------------------------------------------
# Host-compatible session contexts for the frozen workflow runtime.
#
# The monolith's engine/node modules call module-level helpers (``get_db_context``
# / ``get_db_read`` / ``get_async_db_context``) that open sessions from process
# singletons. Ported call sites keep working through the process runtime
# registered by ``create_app``; transaction contracts are identical to the host
# helpers (rollback uncommitted work, never leave an idle-in-transaction
# connection behind).
# ---------------------------------------------------------------------------


@contextmanager
def get_db_context() -> Iterator[Session]:
    """Pool-friendly sync session for read/write paths.

    Never commits or rolls back on its own; the caller owns the transaction
    boundary (host ``get_db_context`` contract).
    """

    from .runtime import get_process_runtime

    with get_process_runtime().database.sync_session() as session:
        yield session


@contextmanager
def get_db_read() -> Iterator[Session]:
    """Read-only sync session.

    Opens a PostgreSQL READ ONLY transaction (accidental writes fail inside
    the database) and always rolls back on exit, so no
    idle-in-transaction connection survives.
    """

    from .runtime import get_process_runtime

    with get_process_runtime().database.sync_session() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


@asynccontextmanager
async def get_async_db_context() -> AsyncIterator[AsyncSession]:
    """Async session context for runtime-plane code (streaming, ``run_sync``).

    Uncommitted transactions roll back on exit; cancellation-safe like the
    host helper (the rollback is shielded so a cancelled caller cannot leak a
    dirty connection).
    """

    from .runtime import get_process_runtime

    async with get_process_runtime().database.async_session() as session:
        yield session


async def end_read_transaction(db: AsyncSession | None, where: str = "") -> None:
    """End the open transaction on ``db`` and return its connection to the pool.

    Use right after a read phase when the next step waits on an LLM or a
    sub-agent for a long time. Sessions are ``expire_on_commit=False``, so ORM
    objects loaded before the commit stay readable; the next statement opens a
    fresh transaction. Failures only log a warning: this is a side-channel
    optimization and must never affect the business path. No-op for ``None``
    or a session without an open transaction.
    """

    if db is None:
        return
    try:
        if not db.in_transaction():
            return
        result = db.commit()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001 - side-channel optimization
        logger.warning("end_read_transaction failed where=%s error=%s", where, exc)


def get_db() -> Iterator[Session]:
    """FastAPI-style sync session dependency (host ``get_db`` shape)."""

    from .runtime import get_process_runtime

    with get_process_runtime().database.sync_session() as session:
        yield session


def get_async_pool_status() -> dict[str, int | float]:
    """Snapshot of the async connection pool for hot-path logging (host shape)."""

    from .runtime import get_process_runtime

    pool = get_process_runtime().database.async_pool()
    total = pool.size() + pool.overflow()
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "total": total,
        "usage_percent": round(pool.checkedout() / total * 100, 2) if total > 0 else 0,
    }
