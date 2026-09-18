import asyncio
import logging
import traceback
from contextlib import contextmanager, asynccontextmanager
from typing import Generator, AsyncGenerator

from sqlalchemy import create_engine, text, event
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker, declarative_base

from app.core.config import settings
from app.core.trace import get_trace_id

SQLALCHEMY_DATABASE_URL = f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
ASYNC_DATABASE_URL = f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    connect_args={
        "options": "-c timezone=UTC -c statement_timeout=60000"
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
logger = logging.getLogger(__name__)


# Dependency to get a DB session (FastAPI Depends 专用)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """
    线程安全、池友好的 Session 上下文（读写场景）。
    不会自动 commit/rollback，调用方自己决定事务边界。
    用法：
        with get_db_context() as db:
            db.add(obj)
            db.commit()          # 或 db.rollback()
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        if db.in_transaction():
            db.rollback()
        db.close()


@contextmanager
def get_db_read() -> Generator[Session, None, None]:
    """
    只读场景专用。
    - 设置 PostgreSQL READ ONLY 事务，PG 跳过 WAL 写入，轻微提升性能
    - 若误写会由 PG 直接报错，防御 bug
    - 出上下文永远 rollback，绝不留下 idle in transaction
    """
    db: Session = SessionLocal(expire_on_commit=False)
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        yield db
    finally:
        db.rollback()
        db.close()


# ==================== Async Engine (工作流专用) ====================

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # async 场景下避免 lazy load 触发同步 IO
)


def _driver_conn(dbapi_connection):
    return (
            getattr(dbapi_connection, "driver_connection", None)
            or getattr(dbapi_connection, "_connection", None)
    )


@event.listens_for(async_engine.sync_engine, "checkout")
def _asyncpg_checkout(dbapi_connection, connection_record, connection_proxy):
    driver = _driver_conn(dbapi_connection)
    if driver is None:
        return

    if getattr(driver, "is_in_transaction", lambda: False)():
        logger.error(
            "[DB_POOL_DIRTY_CHECKOUT] "
            "asyncpg connection is already in transaction "
            "conn_id=%s previous_trace_id=%s "
            "previous_checkout_stack=%s",
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


@event.listens_for(async_engine.sync_engine, "checkin")
def _asyncpg_checkin(dbapi_connection, connection_record):
    if dbapi_connection is None:
        return

    driver = _driver_conn(dbapi_connection)
    if driver is None:
        return

    is_in_transaction = getattr(
        driver, "is_in_transaction", lambda: False
    )()

    if is_in_transaction:
        logger.error(
            "[DB_POOL_DIRTY_CHECKIN] "
            "returning dirty asyncpg connection to pool "
            "conn_id=%s checkout_trace_id=%s "
            "checkout_stack=%s checkin_stack=%s",
            id(driver),
            connection_record.info.get("checkout_trace_id"),
            connection_record.info.get("checkout_stack"),
            "".join(traceback.format_stack(limit=20)),
        )


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends 专用，async 路由使用"""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def get_async_db_context() -> AsyncGenerator[AsyncSession, None]:
    """上下文管理器版本，用于 event_generator 闭包等非 Depends 场景"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                rollback_task = asyncio.create_task(session.rollback())
                try:
                    await asyncio.shield(rollback_task)
                except asyncio.CancelledError:
                    await rollback_task
                    raise
                except Exception:
                    logger.exception("Failed to rollback database session")

            await session.close()


# ==================== 连接池监控 ====================

def get_pool_status():
    """获取连接池状态（用于监控）"""
    pool = engine.pool
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "total": pool.size() + pool.overflow(),
        "usage_percent": round(pool.checkedout() / (pool.size() + pool.overflow()) * 100, 2)
        if (pool.size() + pool.overflow()) > 0 else 0
    }


def get_async_pool_status():
    """获取 async 连接池状态（用于热点链路埋点）"""
    pool = async_engine.pool
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "total": pool.size() + pool.overflow(),
        "usage_percent": round(pool.checkedout() / (pool.size() + pool.overflow()) * 100, 2)
        if (pool.size() + pool.overflow()) > 0 else 0,
    }
