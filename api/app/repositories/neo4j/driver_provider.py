"""Neo4j Driver Provider — 进程级 driver 单例。

设计要点：
  - 每个进程一个 driver，由 lifespan / worker_process_init 触发初始化
  - 配置 max_connection_pool_size 与 connection_acquisition_timeout
  - 提供 get_driver_sync() 给 Neo4jConnector 使用
  - 暴露 close_driver() 给关停钩子
  - 使用 threading.Lock 确保 Celery worker（无事件循环）也能安全调用

风险缓解：
  - NEO4J_USE_SHARED_DRIVER=false 时回退旧模式（每次新建 driver）
  - 默认 pool_size=100 与 memory_tasks concurrency=100 对齐
  - reset_driver_for_fork() 供 prefork 子进程重建
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from neo4j import AsyncDriver, AsyncGraphDatabase, basic_auth

from app.core.config import settings

logger = logging.getLogger(__name__)

_driver: Optional[AsyncDriver] = None
_lock = threading.Lock()


def _is_shared_driver_enabled() -> bool:
    """Feature Flag: 是否启用共享 driver 单例。"""
    return os.getenv("NEO4J_USE_SHARED_DRIVER", "true").lower() != "false"


def _build_driver() -> AsyncDriver:
    """构建 Neo4j AsyncDriver 实例。"""
    if not settings.NEO4J_PASSWORD:
        raise RuntimeError(
            "NEO4J_PASSWORD is not set. Create a .env with NEO4J_PASSWORD or export it before running."
        )

    pool_size = int(os.getenv("NEO4J_POOL_SIZE", "100"))
    acquire_timeout = float(os.getenv("NEO4J_ACQUIRE_TIMEOUT", "60"))
    max_lifetime = int(os.getenv("NEO4J_CONN_MAX_LIFETIME", "1800"))

    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=basic_auth(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
        max_connection_pool_size=pool_size,
        connection_acquisition_timeout=acquire_timeout,
        max_connection_lifetime=max_lifetime,
        keep_alive=True,
    )
    logger.info(
        f"[Neo4jDriver] created pid={os.getpid()} pool={pool_size} "
        f"acquire_timeout={acquire_timeout}s lifetime={max_lifetime}s"
    )
    return driver


def get_driver_sync() -> AsyncDriver:
    """获取进程级 driver 单例（线程安全）。

    使用 threading.Lock 而非 asyncio.Lock，确保在无事件循环的
    Celery worker 上下文中也能安全调用。

    当 NEO4J_USE_SHARED_DRIVER=false 时，每次返回新 driver（旧行为）。

    Celery worker 兼容：每个 event loop 对应一个 driver 实例，
    避免 "Future attached to a different loop" 错误。
    """
    global _driver

    if not _is_shared_driver_enabled():
        return _build_driver()

    # Celery worker 中每个任务可能创建新的 event loop，
    # 需要检测 loop 是否变化，变化时重建 driver。
    import asyncio
    try:
        current_loop = asyncio.get_event_loop()
    except RuntimeError:
        current_loop = None

    if _driver is None:
        with _lock:
            if _driver is None:
                _driver = _build_driver()
                _driver._bound_loop_id = id(current_loop) if current_loop else None
    else:
        # 检测 loop 是否变化
        bound_loop_id = getattr(_driver, '_bound_loop_id', None)
        if current_loop is not None and bound_loop_id is not None and id(current_loop) != bound_loop_id:
            with _lock:
                # double-check
                if getattr(_driver, '_bound_loop_id', None) != id(current_loop):
                    logger.info(
                        f"[Neo4jDriver] event loop changed, rebuilding driver "
                        f"(old_loop={bound_loop_id}, new_loop={id(current_loop)})"
                    )
                    # 不 await close 旧 driver（可能绑定到已关闭的 loop），直接丢弃
                    _driver = _build_driver()
                    _driver._bound_loop_id = id(current_loop)

    return _driver


async def close_driver() -> None:
    """关闭进程级 driver。由 lifespan / worker_process_shutdown 调用。"""
    global _driver
    if _driver is not None:
        try:
            await _driver.close()
            logger.info(f"[Neo4jDriver] closed pid={os.getpid()}")
        except Exception as e:
            logger.warning(f"[Neo4jDriver] close error: {e}")
        finally:
            _driver = None


def reset_driver_for_fork() -> None:
    """prefork 子进程重建 driver 前的清理。

    子进程不可复用父进程 socket，必须置 None 后重建。
    仅在 prefork worker（document_tasks / periodic_tasks）中需要调用。
    memory_tasks 用 --pool=threads，所有线程共享父进程 driver，无需调用。
    """
    global _driver
    _driver = None
    logger.info(f"[Neo4jDriver] reset for fork pid={os.getpid()}")
