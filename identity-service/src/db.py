"""数据库连接层：对齐 core/api/app/db.py 的 async 部分（纯 async，无同步引擎；模型使用本地 identity/models，不依赖 core）。

- URL 由 settings 从 .env 原始变量（DB_HOST 等）拼接（identity/config.py）
- 连接池参数与 core 同名配置对齐（pool_pre_ping/pool_recycle/...）
- connect_args 强制 UTC 时区 + statement_timeout，与老单体连接语义一致（DB 存 naive UTC）
- 惰性初始化（init_db/close_db）：由服务 lifespan 与测试 boot fixture 显式控制
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings  # noqa: E402

engine = None
async_session = None


async def init_db():
    """惰性初始化（幂等）：由服务 lifespan / 测试 boot fixture 显式调用。

    幂等：已初始化则直接返回——重复调用（如测试多次 boot）不会重复建引擎。
    """
    global engine, async_session
    if engine is not None:
        return
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_recycle=settings.DB_POOL_RECYCLE,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        # 与老单体 db.py 语义一致（psycopg2 用 options=-c，asyncpg 用 server_settings）：
        # 连接级 UTC 时区 + 60s 语句超时（防止慢查询挂死连接池）
        connect_args={"server_settings": {"timezone": "UTC", "statement_timeout": "60000"}},
    )
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def close_db():
    if engine is not None:
        await engine.dispose()


def _session():
    # 未 init_db 时给出明确错误：'None' object is not callable 无助于排查
    if async_session is None:
        raise RuntimeError("identity db not initialized: call db.init_db() in lifespan first")
    return async_session()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends 专用（对齐 core db.py get_async_db）：async 路由使用。

    用法：`async def route(session: AsyncSession = Depends(get_async_db))`——
    每个请求一个会话，生命周期由框架管理，无需手动关闭。
    """
    async with _session() as session:
        yield session


@asynccontextmanager
async def get_async_db_context() -> AsyncGenerator[AsyncSession, None]:
    """上下文管理器版本（对齐 core db.py get_async_db_context）：非 Depends 场景使用。

    用法：`async with get_async_db_context() as session:`（后台任务/循环/脚本）。
    事务边界由调用方决定；出异常回滚后重抛，退出前若仍有未提交事务则回滚，
    不留 idle in transaction。此处保留宽捕获是必须的：任何异常都要先回滚再向上抛。
    """
    async with _session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            if session.in_transaction():
                await session.rollback()
