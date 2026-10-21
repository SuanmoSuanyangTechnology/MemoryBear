import asyncio
import inspect
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

# 后台收尾任务的强引用集合，避免 task 被 GC 回收导致收尾中断
_shielded_tasks: set[asyncio.Task] = set()


async def _run_shielded(coro, what: str) -> None:
    """在独立 task 里跑 coro，并对调用方屏蔽取消。

    客户端断开时 Starlette/anyio 的 cancel scope 是电平触发的：被取消的任务里
    每一个 await 都会再次收到 CancelledError，直接 await session.close() 会在
    rollback/归还连接的中途被打断，asyncpg 连接停在事务里（DIRTY_CHECKIN）或
    terminate 被取消（Exception terminating connection）。独立 task 不属于该
    cancel scope，shield 保证外层被取消时它仍会跑完；外层仍会收到 CancelledError。
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
    """close() 对取消免疫的 AsyncSession，保证连接一定被干净归还连接池。

    只覆盖 close()：它会回滚未提交事务并归还连接；rollback() 保持原语义不动。
    """

    async def close(self) -> None:
        await _run_shielded(super().close(), "AsyncSession.close")


AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=CancelSafeAsyncSession,
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
        # 软失效：连接不再被复用，下次该槽位 checkout 时在正常上下文里重建，
        # 避免脏连接被下一个请求拿到（此处不能同步 terminate，可能处于被取消的上下文）
        connection_record.invalidate(
            Exception("dirty asyncpg connection on checkin"), soft=True
        )
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


async def end_read_transaction(db, where: str = "") -> None:
    """结束 db 上已开启的事务，把底层连接还给连接池。

    用在"读完库、接下来要长时间等 LLM / 子 Agent"之前。AsyncSessionLocal 是
    expire_on_commit=False，commit 后已加载的 ORM 对象仍可正常读属性（与 close() 不同，
    不会脱离 session），后续再用 db 时会自动开新事务。

    失败只记 warning，不影响主流程。db 为 None / 无事务时什么也不做。
    """
    if db is None:
        return
    try:
        if not db.in_transaction():
            return
        result = db.commit()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001 - 旁路优化，不能影响业务
        logger.warning("end_read_transaction failed where=%s error=%s", where, exc)


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
