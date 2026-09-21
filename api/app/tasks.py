from app.core.memory.channel_policy import require_neo4j_memory
import asyncio
import json
import os
import socket
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import logging

import redis
from billiard.exceptions import SoftTimeLimitExceeded
from celery import current_task
from celery.signals import after_setup_logger
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from sqlalchemy import String, cast, select

from app.aioRedis import get_thread_safe_redis, get_thread_safe_sync_redis
from app.celery_app import celery_app
from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.memory.exceptions import MemoryExtractionBusinessError
from app.core.memory.storage.outbox.consumer import cleanup_outbox_events, consume_outbox_batch
from app.core.memory.storage.outbox.exceptions import safe_error
from app.core.memory.storage_services.reflection_engine import retry_registry as rr
from app.core.memory.storage_services.forgetting_engine.constants import FORGET_CANDIDATES_KEY as _FORGET_CANDIDATES_KEY, FORGET_INFLIGHT_KEY as _FORGET_INFLIGHT_KEY
from app.core.memory.storage_services.reflection_engine.errors import ReflectionBusinessError, ReflectionFailureReason, ReflectionRetriesExhausted
# Import a unified Celery instance
from app.core.utils.datetime_utils import as_utc_aware, parse_iso_to_utc_naive, to_iso_z, utcnow, utcnow_naive
from app.db import get_db_context, get_db_read
from app.models import App, AppRelease, User
from app.models.end_user_model import EndUser
from app.repositories.end_user_repository import get_active_end_users_by_workspace, get_end_users_by_workspace, get_all_active_workspaces
from app.services.memory_config_service import MemoryConfigService
from app.utils.redis_lock import UNLOCK_SCRIPT, RedisFairLock


class CeleryTaskIdFilter(logging.Filter):
    def filter(self, record):
        try:
            record.task_id = current_task.request.id
        except Exception:
            record.task_id = "-"
        return True


@after_setup_logger.connect
def setup_logger(logger, *args, **kwargs):
    formatter = logging.Formatter(
        "[%(asctime)s: %(levelname)s/%(processName)s] "
        "[task_id=%(task_id)s] %(message)s"
    )

    for handler in logger.handlers:
        handler.setFormatter(formatter)
        handler.addFilter(CeleryTaskIdFilter())


logger = get_logger(__name__)

# ── 预编译文件类型正则 & 常量 ──────────────────────────────────
# Embedding 并发写入的最大线程数，需根据模型 API rate limit 调整
# auto_questions LLM 并发调用的最大线程数
# 文档解析页数上限

# ── GDS 拓扑分数（eigenvector 中心性）扫描/计算常量 ──────────────
_GDS_TOPOLOGY_INFLIGHT_KEY_FMT = "gds_topology:inflight:{end_user_id}"


# Redis keys for document parse task tracking


# 模块级同步 Redis 连接池，供 Celery 任务共享使用
# 连接 CELERY_BACKEND DB，与 write_message:last_done 时间戳写入保持一致
# 使用连接池而非单例客户端，提供更好的并发性能和自动重连
_sync_redis_pool: redis.ConnectionPool | None = None


def _get_or_create_redis_pool() -> redis.ConnectionPool | None:
    """获取或创建 Redis 连接池（懒初始化）"""
    global _sync_redis_pool
    if _sync_redis_pool is None:
        try:
            _sync_redis_pool = redis.ConnectionPool(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB_CELERY_BACKEND,
                password=settings.REDIS_PASSWORD,
                decode_responses=True,
                max_connections=100,
                socket_connect_timeout=5,
                socket_timeout=10,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            logger.info("Redis connection pool created for Celery tasks")
        except Exception as e:
            logger.error(f"Failed to create Redis connection pool: {e}", exc_info=True)
            return None
    return _sync_redis_pool


def get_sync_redis_client() -> Optional[redis.StrictRedis]:
    """获取同步 Redis 客户端（使用连接池）

    依赖连接池本身的 ``health_check_interval=30`` 做健康检查；
    每次取客户端不再发 ``PING``，避免在热路径上多一次 RTT。
    冷启动应通过 ``warmup_sync_redis_pool`` 预热，避免首次请求承担建池+握手成本。

    Returns:
        redis.StrictRedis: Redis 客户端实例；当连接池创建失败时返回 None。
    """
    try:
        pool = _get_or_create_redis_pool()
        if pool is None:
            return None
        return redis.StrictRedis(connection_pool=pool)
    except RedisError as e:
        logger.error(f"Redis connection failed: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting Redis client: {e}", exc_info=True)
        return None


def warmup_sync_redis_pool() -> bool:
    """应用启动时预热 Redis 连接池。

    复用 ``get_sync_redis_client`` 构造客户端，再发一次 ``PING`` 完成 TCP 握手，
    把"首次请求需要建池"的 50–200ms 冷启动开销前置到启动阶段。
    任何失败都只记录日志，不影响进程启动。

    Returns:
        bool: 预热成功返回 True；失败或 Redis 不可用返回 False。
    """
    try:
        client = get_sync_redis_client()
        if client is None:
            return False
        client.ping()
        logger.info("Sync Redis pool warmed up (PING ok)")
        return True
    except RedisError as e:
        logger.warning(f"Sync Redis pool warmup failed: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected error warming Sync Redis pool: {e}")
        return False


def set_asyncio_event_loop():
    """Ensure an open asyncio event loop exists for the current thread.

    Reuses the existing event loop if one is available and still open.
    Creates and installs a new event loop only when the current one is
    closed or missing (e.g. after ``_shutdown_loop_gracefully``).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _shutdown_loop_gracefully(loop: asyncio.AbstractEventLoop):
    """Cancel pending tasks and finalize async generators, but keep the loop open for reuse.

    Not closing the loop avoids 'Event loop is closed' from httpx AsyncClient.__del__ during GC.
    """
    try:
        # Cancel remaining tasks to prevent leaks between Celery tasks
        all_tasks = asyncio.all_tasks(loop)
        if all_tasks:
            for task in all_tasks:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*all_tasks, return_exceptions=True))
        # Finalize async generators so network/client resources are properly cleaned up.
        # This does NOT close the loop.
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:
        pass


@celery_app.task(
    name="app.tasks.scan_outbox_projection",
    queue="memory_projection",
    max_retries=0,
)
def scan_outbox_projection():
    worker_id = f"{socket.gethostname()[:60]}:{os.getpid()}:{uuid.uuid4()}"
    try:
        result = asyncio.run(
            consume_outbox_batch(settings.OUTBOX_BATCH_SIZE, worker_id)
        )
    except Exception as exc:
        # Celery 会记录抛出的异常；剥离驱动 SQL、凭据与负载。
        error = safe_error(exc, settings.OUTBOX_ERROR_MAX_LENGTH)
        logger.error("Outbox task failed: %s", error)
        raise RuntimeError(f"Outbox task failed: {error}") from None
    logger.info("Outbox task completed: %s", result)
    return result


@celery_app.task(
    name="app.tasks.cleanup_outbox",
    queue="memory_projection",
    max_retries=0,
)
def cleanup_outbox():
    try:
        result = asyncio.run(
            cleanup_outbox_events(settings.OUTBOX_BATCH_SIZE)
        )
    except Exception as exc:
        # Celery 会记录抛出的异常；剥离驱动 SQL、凭据与负载。
        error = safe_error(exc, settings.OUTBOX_ERROR_MAX_LENGTH)
        logger.error("Outbox task failed: %s", error)
        raise RuntimeError(f"Outbox task failed: {error}") from None
    logger.info("Outbox task completed: %s", result)
    return result


@celery_app.task(name="tasks.process_item")
def process_item(item: dict):
    """
    A simulated long-running task that processes an item.
    In a real-world scenario, this could be anything:
    - Sending an email
    - Generating a report
    - Performing a complex calculation
    - Calling a third-party API
    """
    print(f"Processing item: {item['name']}")
    # Simulate work for 5 seconds
    time.sleep(5)
    result = f"Item '{item['name']}' processed successfully at a price of ${item['price']}."
    print(result)
    return result


@celery_app.task(name="app.core.memory.agent.write_message", bind=True, acks_late=False, max_retries=0)
def write_message_task(
        self,
        end_user_id: str,
        target_message: Optional[dict] = None,
        context_before: Optional[List[dict]] = None,
        context_after: Optional[List[dict]] = None,
        config_id: str = "",
        workspace_id: str = "",
        conversation_id: str = "",
        message_seq: int = 0,
        language: str = "zh",
        skip_cursor_advance: bool = False,
        dispatch_at: str = "",  # 任务执行时间
        source: str = "",  # 写入来源（agent/service_api/mcp/workflow）
        # MCP 入口兼容字段（不经过 memory_messages 表，直接写入）
        messages: Optional[List[dict]] = None,
        storage_type: str = "neo4j",
        user_rag_memory_id: str = "",
) -> Dict[str, Any]:
    """统一写入任务 — 纯净入口，接收完整参数直接写入。

    Args:
        end_user_id: 终端用户 ID（分片键）
        target_message: 目标消息 {"role": "user", "content": "...", "dialog_at": "..."}
        context_before: 上文消息列表
        context_after: 下文消息列表
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        conversation_id: 对话 ID
        message_seq: 消息序号
        language: 语言
        skip_cursor_advance: 是否跳过 cursor 推进（MCP 等直接写入路径）
        dispatch_at: 任务派发时刻的 UTC ISO 8601 时间戳，由 push_write_task 自动注入
        messages: MCP 入口兼容字段，单条消息列表 [{"role", "content", "dialog_at"}]
        storage_type: MCP 入口兼容字段，存储类型（neo4j / rag）
        user_rag_memory_id: MCP 入口兼容字段，RAG 记忆 ID

    Returns:
        Dict containing status, result, elapsed_time, task_id
    """
    require_neo4j_memory(storage_type)
    loop = set_asyncio_event_loop()
    # MCP 入口兼容：收到 messages 但无 target_message 时，转换为新格式
    if target_message is None and messages:
        msg = messages[0] if messages else {"role": "user", "content": ""}
        target_message = msg
        context_before = []
        context_after = []
        skip_cursor_advance = True

    # 解析 end_user_id：若排队期间用户已被合并，自动路由到目标用户
    resolved_end_user_id = end_user_id
    try:
        with get_db_context() as db:
            from app.repositories.end_user_repository import EndUserRepository
            repo = EndUserRepository(db)
            resolved = repo.resolve_merge_by_origin_id(uuid.UUID(end_user_id))
            if resolved:
                logger.info(
                    f"[CELERY WRITE] end_user_id merged, redirecting: "
                    f"{end_user_id} → {resolved.id}"
                )
                resolved_end_user_id = str(resolved.id)
    except Exception as e:
        logger.warning(
            f"[CELERY WRITE] merge resolution failed for {end_user_id}: {e}, "
            f"falling back to original ID"
        )

    # RAG 存储类型走独立路径

    # 新格式：直接调用 MemoryService.write()
    logger.info(
        f"[CELERY WRITE] Starting - end_user_id={resolved_end_user_id}, "
        f"config_id={config_id}, conv={conversation_id or '-'}, "
        f"seq={message_seq}, language={language}"
    )
    start_time = time.time()

    async def _run():
        from app.core.memory.memory_service import MemoryService

        service = MemoryService(
            config_id=uuid.UUID(config_id),
            end_user_id=resolved_end_user_id,
            workspace_id=workspace_id,
            language=language,
        )

        result = await service.write(
            target_message=target_message or {"role": "user", "content": ""},
            context_before=context_before or [],
            context_after=context_after or [],
            conversation_id=conversation_id,
            message_seq=message_seq,
            language=language,
            skip_cursor_advance=skip_cursor_advance,
            dispatch_at=dispatch_at,
            source=source,
        )
        return result

    try:
        task_start_time = int(time.time())

        write_result = loop.run_until_complete(_run())
        if write_result.degraded_error is not None:
            from app.core.memory.alerts import enqueue_memory_extraction_alert_safely

            loop.run_until_complete(
                enqueue_memory_extraction_alert_safely(
                    error=write_result.degraded_error,
                    memory_message_id=str(
                        (target_message or {}).get("memory_message_id") or ""
                    ),
                    workspace_id=workspace_id,
                    end_user_id=resolved_end_user_id,
                    source=source,
                    task_id=str(self.request.id or ""),
                )
            )
        result = {
            "status": write_result.status,
            "extraction": write_result.extraction,
        }
        elapsed_time = time.time() - start_time

        logger.info(f"[CELERY WRITE] Task completed - elapsed_time={elapsed_time:.2f}s")

        # 记录最近一次写入完成时间戳
        redis_client = get_sync_redis_client()
        try:
            if redis_client is not None:
                from datetime import timezone as _tz
                _now_utc = to_iso_z(datetime.now(_tz.utc))
                redis_client.set(f"write_message:last_done:{resolved_end_user_id}", _now_utc, ex=86400 * 30)
        except Exception as _e:
            logger.warning(f"[CELERY WRITE] 写入 last_done 时间戳失败: {_e}")

        # 同步 end_user 记忆计数
        try:
            from app.core.memory.utils.memory_count_utils import sync_end_user_memory_count_from_neo4j
            from app.repositories.neo4j.neo4j_connector import Neo4jConnector

            async def _sync_count():
                connector = Neo4jConnector()
                try:
                    return await sync_end_user_memory_count_from_neo4j(resolved_end_user_id, connector)
                finally:
                    await connector.close()

            loop.run_until_complete(_sync_count())
        except Exception as _count_e:
            logger.warning(f"[CELERY WRITE] 同步记忆计数失败: {_count_e}")

        # 刷新「最后写入时间」：用于反思活跃用户判断，覆盖 API/MCP 等不更新 conversations 行的写入
        try:
            from app.services.memory_reflection_service import WorkspaceAppService
            with get_db_context() as db:
                WorkspaceAppService(db).update_end_user_write_time(resolved_end_user_id)
        except Exception as _wt_e:
            logger.warning(f"[CELERY WRITE] 更新 write_time 失败: {_wt_e}")

        try:
            safe_result = jsonable_encoder(result)
        except Exception:
            safe_result = str(result)

        return {
            "status": "SUCCESS",
            "result": safe_result,
            "start_at": task_start_time,
            "end_user_id": end_user_id,
            "config_id": str(config_id) if config_id else None,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id,
        }
    except BaseException as e:
        from app.schemas.memory_config_schema import (
            ModelNotFoundError,
            ModelInactiveError,
            InvalidConfigError,
        )

        elapsed_time = time.time() - start_time
        if hasattr(e, 'exceptions'):
            error_messages = [f"{type(sub_e).__name__}: {str(sub_e)}" for sub_e in e.exceptions]
            detailed_error = "; ".join(error_messages)
        else:
            detailed_error = str(e)

        logger.error(f"[CELERY WRITE] Task failed - elapsed_time={elapsed_time:.2f}s, error={detailed_error}",
                     exc_info=True)

        # 只有主萃取链路的稳定业务异常会生成用户级告警。WritePipeline 的
        # finally 已在异常到达此处前完成摘要任务取消和资源清理。
        if isinstance(e, MemoryExtractionBusinessError):
            from app.core.memory.alerts import enqueue_memory_extraction_alert_safely

            loop.run_until_complete(
                enqueue_memory_extraction_alert_safely(
                    error=e,
                    memory_message_id=str(
                        (target_message or {}).get("memory_message_id") or ""
                    ),
                    workspace_id=workspace_id,
                    end_user_id=resolved_end_user_id,
                    source=source,
                    task_id=str(self.request.id or ""),
                )
            )

        # 配置类确定性错误：直接 raise，让 Celery 将任务标记为 FAILURE。
        if isinstance(e, (ModelNotFoundError, ModelInactiveError, InvalidConfigError)):
            logger.error(
                f"[CELERY WRITE] Configuration error detected, task will not be retried - "
                f"error_type={type(e).__name__}, error={detailed_error}"
            )
            raise

        # 瞬时错误（网络超时、Neo4j 死锁等）：交由 Celery 按 max_retries 自动重试
        raise self.retry(exc=e)
    finally:
        if loop:
            _shutdown_loop_gracefully(loop)


@celery_app.task(
    name="app.core.memory.fast_write_message",
    bind=True,
    acks_late=False,
    max_retries=0
)
def fast_write_message_task(
        self,
        end_user_id: str,
        target_message: Optional[dict] = None,
        config_id: str = "",
        workspace_id: str = "",
        conversation_id: str = "",
        message_seq: int = 0,
        language: str = "zh",
        dispatch_at: str = "",
        source: str = "",
) -> Dict[str, Any]:
    """快速写入任务 — 构造 MemoryService 并驱动 fast_write。

    职责：提供事件循环 + 计时 + backend 状态映射，不夹带业务加载逻辑。

    backend 状态与业务结果分层：
    - ``success`` / ``dropped`` 是 Pipeline 业务结果，放在返回值的 ``result`` 中；
      任务正常返回时 backend 为 ``SUCCESS``。
    - 持久化 / 配置 / 代码异常必须抛出任务函数，backend 才会标记 ``FAILURE``，
      scheduler tracker 与失败率监控才能拿到真实状态。
    - ``max_retries=0``：不做 Celery 层重试；Neo4j deadlock 的有界重试在 Pipeline 内完成。

    Args:
        end_user_id: 终端用户 ID（分片键）
        target_message: 目标消息 {"role": "user", "content": "...", "dialog_at": "..."}
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        conversation_id: 对话 ID（会话类入口非空，用于确定性 ID 生成）
        message_seq: 消息序号
        language: 语言
        dispatch_at: 任务派发时刻的 UTC ISO 8601 时间戳
        source: 写入来源（agent/service_api/mcp/workflow）

    Returns:
        Dict containing status, result, task_id
    """
    # 解析 end_user_id：若排队期间用户已被合并，自动路由到目标用户
    resolved_end_user_id = end_user_id
    try:
        with get_db_context() as db:
            from app.repositories.end_user_repository import EndUserRepository
            repo = EndUserRepository(db)
            resolved = repo.resolve_merge_by_origin_id(uuid.UUID(end_user_id))
            if resolved:
                logger.info(
                    f"[CELERY FAST WRITE] end_user_id merged, redirecting: "
                    f"{end_user_id} → {resolved.id}"
                )
                resolved_end_user_id = str(resolved.id)
    except Exception as e:
        logger.warning(
            f"[CELERY FAST WRITE] merge resolution failed for {end_user_id}: {e}, "
            f"falling back to original ID"
        )

    logger.info(
        f"[CELERY FAST WRITE] Starting - end_user_id={resolved_end_user_id}, "
        f"config_id={config_id}, conv={conversation_id or '-'}, "
        f"seq={message_seq}, language={language}, source={source or '-'}"
    )
    start_time = time.time()

    scene_context_holder: dict[str, Any] = {}

    async def _run() -> dict:
        from app.core.memory.memory_service import MemoryService
        from app.core.memory.scene.scene_boundary_service import SceneBoundaryService

        service = MemoryService(
            config_id=uuid.UUID(config_id),
            end_user_id=resolved_end_user_id,
            workspace_id=workspace_id,
            language=language,
        )
        scene_context = None
        memory_message_id = str((target_message or {}).get("memory_message_id") or "")
        if memory_message_id and str((target_message or {}).get("role") or "") == "user":
            with get_db_context() as db:
                scene_context = SceneBoundaryService.prepare_context(
                    db,
                    resolved_end_user_id=resolved_end_user_id,
                    memory_message_id=memory_message_id,
                    config=service.ctx.memory_config,
                )
        scene_context_holder["context"] = scene_context

        return await service.fast_write(
            target_message=target_message or {"role": "user", "content": ""},
            conversation_id=conversation_id,
            message_seq=message_seq,
            source=source,
            dispatch_at=dispatch_at,
            scene_context=scene_context,
        )

    loop = None
    try:
        loop = set_asyncio_event_loop()

        result = loop.run_until_complete(_run())

        scene_context = scene_context_holder.get("context")
        scene_decision = result.get("scene_decision") if isinstance(result, dict) else None
        if scene_context is not None and scene_decision:
            from app.core.memory.scene.scene_boundary_service import SceneBoundaryService

            summary_claimed = False
            with get_db_context() as db:
                _updated, persisted = SceneBoundaryService.save_initial_decision(
                    db, context=scene_context, decision=scene_decision
                )
                if persisted == "SHIFTED" and scene_context.previous_shifted_message_id:
                    summary_claimed = SceneBoundaryService.claim_summary(
                        db,
                        scene_start_message_id=scene_context.previous_shifted_message_id,
                        end_user_id=resolved_end_user_id,
                    )
                db.commit()
            if summary_claimed:
                try:
                    async_result = generate_scene_summary.apply_async(
                        kwargs={
                            "end_user_id": resolved_end_user_id,
                            "config_id": config_id,
                            "scene_start_message_id": scene_context.previous_shifted_message_id,
                            "close_before_message_id": scene_context.current_message_id,
                            "close_reason": "SHIFTED",
                        }
                    )
                    logger.info(
                        "[SceneSummary] fastwrite task dispatched: scene_start=%s, "
                        "close_before=%s, task_id=%s",
                        scene_context.previous_shifted_message_id,
                        scene_context.current_message_id,
                        async_result.id,
                    )
                except Exception:
                    logger.exception(
                        "[SceneSummary] fastwrite task dispatch failed, releasing claim: "
                        "scene_start=%s, close_before=%s",
                        scene_context.previous_shifted_message_id,
                        scene_context.current_message_id,
                    )
                    with get_db_context() as db:
                        SceneBoundaryService.release_summary_claim(
                            db,
                            scene_start_message_id=scene_context.previous_shifted_message_id,
                            end_user_id=resolved_end_user_id,
                        )
                        db.commit()
                    raise

        elapsed_time = time.time() - start_time

        logger.info(f"[CELERY FAST WRITE] Task completed - elapsed_time={elapsed_time:.2f}s")

        try:
            safe_result = jsonable_encoder(result)
        except Exception:
            safe_result = str(result)

        return {
            "status": "SUCCESS",
            "result": safe_result,
            "task_id": self.request.id,
        }
    except BaseException:
        elapsed_time = time.time() - start_time
        logger.exception(f"[CELERY FAST WRITE] Failed - elapsed_time={elapsed_time:.2f}s")
        # 异常必须逃出任务函数，Celery backend 才会标记 FAILURE
        raise
    finally:
        if loop:
            _shutdown_loop_gracefully(loop)


def _is_active_recently(db, end_user_id: str, inactive_hours: int | None = None) -> bool:
    """用户是否活跃：end_user.write_time 距今 < inactive_hours 小时（NULL 或读取失败视为不活跃）。

    write_time 由 write_message_task 写入成功后刷新，覆盖 API / MCP 等全部写入路径。
    inactive_hours 为 None 时取 settings.REFLECT_LAYER2_INACTIVE_HOURS。
    """
    from app.services.memory_reflection_service import WorkspaceAppService

    if inactive_hours is None:
        inactive_hours = settings.REFLECT_LAYER2_INACTIVE_HOURS

    last_write = WorkspaceAppService(db).get_end_user_write_time(end_user_id)
    if last_write is None:
        return False
    last_write = as_utc_aware(last_write).replace(tzinfo=None)
    return (utcnow_naive() - last_write).total_seconds() / 3600 < inactive_hours


def _should_skip_reflection_by_inactivity(db, end_user_id: str, inactive_hours: int | None = None) -> bool:
    """低频反思前置过滤：不活跃则跳过（True=跳过，False=执行）。

    仅按 write_time 做活跃过滤，不含周期判断——低频全量去重的增量节奏由
    run_dedup_full_scan 内部按实体类型的扫描时间自行控制。
    """
    return not _is_active_recently(db, end_user_id, inactive_hours)


def _should_reflect_now(db, end_user_id: str, reflection_time, iteration_period: int) -> bool:
    """高频反思：判断该用户现在是否需要反思。scan 派发前和 do 执行前都用它（保证一致 + 幂等）。

    放行需同时满足：活跃（_is_active_recently，口径 write_time）+ 
    到周期（距上次反思 reflection_time >= iteration_period 小时）。
    reflection_time 为 None 表示从未反思，活跃即放行（首次反思）。
    """
    if not _is_active_recently(db, end_user_id):
        return False  # 不活跃（无 write_time 或距今超阈值）→ 无需反思

    now = utcnow_naive()
    if reflection_time is None:
        return True  # 从未反思：活跃即放行（首次反思）

    reflection_time = as_utc_aware(reflection_time).replace(tzinfo=None)  # 统一 naive UTC
    period_reached = (now - reflection_time).total_seconds() / 3600 >= iteration_period  # 距上次反思够周期
    return period_reached


@celery_app.task(
    name="app.tasks.scan_layer2_reflection",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_layer2_reflection(self) -> Dict[str, Any]:
    """高频反思扫描器：遍历所有用户，筛选出需要反思的，派发 do_layer2_reflection。
    轻量、无事件循环、无单例锁、无超时。
    """
    start_time = time.time()
    from app.models.workspace_model import Workspace

    redis_client = get_sync_redis_client()
    dispatched = 0
    dispatched_user_ids = []
    skip_period_or_new = 0
    skip_inflight = 0

    # db-session 规范：先用只读短 session 取 workspace 列表，
    # 再【按 workspace 粒度】开独立 session，处理完即释放，避免 identity-map 累积。
    with get_db_read() as db:
        workspace_ids = [str(w.id) for w in db.query(Workspace.id).all()]

    active_since = utcnow_naive() - timedelta(hours=settings.REFLECT_LAYER2_INACTIVE_HOURS)
    for ws_id in workspace_ids:
        ws_id_uuid = uuid.UUID(ws_id)
        with get_db_context() as db:
            memory_config_service = MemoryConfigService(db)
            try:
                config_id = memory_config_service.get_workspace_active_config_id(ws_id_uuid)
                config = memory_config_service.load_memory_config(config_id)
            except Exception as e:
                # 单个 workspace 配置异常（无启用配置 / 缺 embedding / 模型被删）只跳过该 workspace
                logger.warning(f"高频反思scan 跳过配置异常的 workspace={ws_id}: {e}")
                continue
            iteration_period = config.reflexion_iteration_period or 24
            # 活跃性（write_time）已在 DB 层过滤，此处仅按 reflection_time 判周期
            for user in get_active_end_users_by_workspace(db, ws_id_uuid, active_since):
                uid = str(user.id)
                try:
                    rt = user.reflection_time
                    if rt is not None:
                        rt_naive = as_utc_aware(rt).replace(tzinfo=None)
                        if (utcnow_naive() - rt_naive).total_seconds() / 3600 < iteration_period:
                            skip_period_or_new += 1
                            continue
                    # 在途锁：抢不到说明该用户已有反思任务在途，跳过（纯 SET NX EX 粗过滤）
                    if redis_client is not None:
                        ok = redis_client.set(
                            f"reflection:inflight:{uid}", "1", nx=True, ex=1500,
                        )
                        if not ok:
                            skip_inflight += 1
                            continue
                    do_layer2_reflection.apply_async(
                        kwargs={
                            "end_user_id": uid,
                            "config_id": str(config_id),
                            "workspace_id": ws_id,
                            "iteration_period": iteration_period,
                        },
                        queue="reflection_tasks",
                    )
                    dispatched += 1
                    dispatched_user_ids.append(uid)
                    # 每派发 10 个用户打印一次进度
                    if dispatched % 10 == 0:
                        logger.info(
                            f"scan_layer2_reflection 进度: 已派发 {dispatched} 个用户, "
                            f"最近10个: {dispatched_user_ids[-10:]}"
                        )
                except Exception as e:
                    logger.error(f"高频反思scan 处理用户失败 user={uid}: {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass

    logger.info(
        f"scan_layer2_reflection 完成: 派发 {dispatched} {dispatched_user_ids}, "
        f"跳过(未到周期/无新增) {skip_period_or_new}, 在途 {skip_inflight}, "
        f"耗时 {time.time() - start_time:.1f}s"
    )
    return {"status": "SUCCESS", "dispatched": dispatched,
            "dispatched_user_ids": dispatched_user_ids,
            "skip_period_or_new": skip_period_or_new, "skip_inflight": skip_inflight}


def _report_reflection_failure(
        *,
        task_type: str,
        end_user_id: str,
        workspace_id: str,
        reason_code: str | None,
        model_type: str | None,
        failed_operations: List[str],
        last_failed_at_ms: int | None,
) -> None:
    """把重试耗尽事件交给可选插件；社区版未注册时静默跳过。"""
    if reason_code is None or last_failed_at_ms is None:
        return

    from app.plugins import get_plugin

    reporter = get_plugin("reflection_failure_reporter")
    if reporter is None:
        return
    try:
        reporter.report(
            task_type=task_type,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            reason_code=reason_code,
            model_type=model_type,
            failed_operations=failed_operations,
            last_failed_at_ms=last_failed_at_ms,
        )
    except Exception:
        logger.error("反思最终失败上报插件执行失败", exc_info=True)


@celery_app.task(
    name="app.tasks.do_layer2_reflection",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def do_layer2_reflection(self, end_user_id: str | None = None, config_id: str = "",
                         workspace_id: str = "", iteration_period: int = 24,
                         from_retry: bool = False, user_id: str | None = None) -> Dict[str, Any]:
    """对【单个用户】执行一次 Layer2 反思（实体去重 / 描述合并 / 未识别实体处理等）。

    由 scan_layer2_reflection 派发，每个用户一个独立任务、独立 db session，跑完即释放内存。
    返回 status 取值：
        success            反思成功执行
        skipped_idempotent 执行前发现已不需要反思（排队期间被别的任务做过）
        lock_timeout       抢用户写锁超时，本次放弃（下一轮 scan 会重派）
        failed             执行报错
    """
    # HACK: 兼容旧参数 user_id，v0.3.15 后移除
    end_user_id = end_user_id or user_id
    if not end_user_id:
        raise ValueError("end_user_id is required")

    start_time = time.time()
    inflight_key = f"reflection:inflight:{end_user_id}"

    async def _run() -> Dict[str, Any]:
        from app.services.memory_reflection_service import WorkspaceAppService
        from app.core.memory.memory_service import MemoryService

        # 步骤1 执行前再判一次是否真的要反思：
        #   任务从 scan 派发到这里可能排队了一段时间，期间该用户可能已被别的
        #   反思任务处理过（reflection_time 已更新），这里复判避免重复反思。
        #   from_retry=True（重试派发）跳过活跃/周期幂等门，否则刚被闸门挡掉的用户重派进来又被自己挡掉。
        if not from_retry:
            with get_db_read() as db:
                ws_svc = WorkspaceAppService(db)
                rt = ws_svc.get_end_user_reflection_time(end_user_id)
                if not _should_reflect_now(db, end_user_id, rt, iteration_period):
                    return {"status": "skipped_idempotent"}

        # 步骤2 抢该用户的写锁：与该用户的记忆写入 pipeline、去重任务互斥，
        #   保证同一用户的图谱不被并发修改。抢不到（超时30s）就本次放弃。
        write_lock = None
        redis_client = get_sync_redis_client()
        if redis_client is not None:
            write_lock = RedisFairLock(
                key=f"memory_write:{end_user_id}",
                redis_client=redis_client,
                expire=600, timeout=30, auto_renewal=True,
            )
            if not await asyncio.to_thread(write_lock.acquire):
                logger.warning(f"反思高频do 获取写锁超时，跳过 user={end_user_id}")
                return {"status": "lock_timeout"}
        try:
            # 步骤2.5 double-check：拿到写锁后再复查一次是否仍需反思。
            #   并发下（concurrency>1 或多 worker 副本）另一个 do 可能在我们抢锁
            #   期间已完成同一用户的反思并刷新了 reflection_time，
            #   不满足则放弃，避免同一批数据被反思两次。from_retry 同样跳过该门。
            if not from_retry:
                with get_db_read() as db:
                    ws_svc = WorkspaceAppService(db)
                    rt_recheck = ws_svc.get_end_user_reflection_time(end_user_id)
                    if not _should_reflect_now(db, end_user_id, rt_recheck, iteration_period):
                        logger.info(f"反思高频do 拿锁后复查已无需反思，跳过 user={end_user_id}")
                        return {"status": "skipped_idempotent"}

            # 步骤2.8 开工租约：通过幂等门 + 抢到写锁后、run() 前登记，进程被硬杀也能被租约兜底重派。
            _rc = get_sync_redis_client()
            rr.lease(_rc, "high_freq", end_user_id,
                     {"config_id": config_id, "workspace_id": workspace_id,
                      "iteration_period": iteration_period},
                     from_retry=from_retry)

            # 步骤3 执行反思（读图谱 → LLM → 写回，全程持锁）
            memory_service = MemoryService(
                config_id=uuid.UUID(config_id),
                end_user_id=end_user_id,
                workspace_id=workspace_id,
            )
            r = await memory_service.run_reflection_layer2()

            completion = rr.completion_of_layer2(r)
            progressed = rr.progressed_layer2(r)

            unresolved_info = r.get("unresolved_entity", {})
            alias_info = r.get("alias_merge", {})
            dedup_info = r.get("entity_dedup", {})
            meta_info = r.get("metadata_extraction", {})
            merge_info = r.get("description_merge", {})
            reason_codes = rr.reason_codes_of_layer2(r)
            primary_reason = rr.select_primary_reason(reason_codes)
            primary_model_type = rr.select_primary_model_type(
                reason_codes, rr.model_types_of_layer2(r), primary_reason
            )

            if completion == "full":
                # 步骤4 完整跑完：刷新"上次反思时间"，注销重试登记
                with get_db_context() as db:
                    WorkspaceAppService(db).update_end_user_reflection_time(end_user_id)
                rr.resolve(_rc, "high_freq", end_user_id)
                logger.info(
                    f"反思高频do 完成 user={end_user_id} status=success "
                    f"未识别解析={unresolved_info.get('resolved', 0)}/{unresolved_info.get('total', 0)} "
                    f"别名归并={alias_info.get('alias_merged', 0)} "
                    f"实体去重={dedup_info.get('merged_count', 0)}(候选{dedup_info.get('candidate_count', 0)}) "
                    f"元数据提取={meta_info.get('extracted', 0)} "
                    f"描述合并={merge_info.get('merged_count', 0)}(候选{merge_info.get('candidate_count', 0)}) "
                    f"耗时={time.time() - start_time:.1f}s"
                )
                # 返回各步骤关键计数（扁平标量，便于 Flower / 调用方一眼查看）
                return {
                    "status": "success",
                    "unresolved_resolved": unresolved_info.get("resolved", 0),
                    "alias_merged": alias_info.get("alias_merged", 0),
                    "dedup_merged": dedup_info.get("merged_count", 0),
                    "metadata_extracted": meta_info.get("extracted", 0),
                    "desc_merged": merge_info.get("merged_count", 0),
                }

            # partial：有步骤被熔断跳过/超时。已有推进，刷新 reflection_time（重派交给重试队列独占）。
            # failed 不会到这里（真异常冒到外层 except 处理）。
            if completion == "partial":
                with get_db_context() as db:
                    WorkspaceAppService(db).update_end_user_reflection_time(end_user_id)
            skipped_steps = rr.skipped_steps_of_layer2(r)
            record_result = rr.record(
                _rc,
                "high_freq",
                end_user_id,
                completion,
                progressed,
                skipped_steps=skipped_steps,
                reason_code=primary_reason,
                model_type=primary_model_type,
            )
            if record_result.outcome is rr.RetryRecordOutcome.EXHAUSTED:
                _report_reflection_failure(
                    task_type="high_freq",
                    end_user_id=end_user_id,
                    workspace_id=workspace_id,
                    reason_code=primary_reason,
                    model_type=primary_model_type,
                    failed_operations=skipped_steps,
                    last_failed_at_ms=record_result.last_failed_at_ms,
                )
                if primary_reason is not None:
                    raise ReflectionRetriesExhausted(
                        ReflectionFailureReason(primary_reason)
                    )
            logger.warning(
                f"反思高频do 未完整完成 user={end_user_id} completion={completion} "
                f"progressed={progressed} skipped={skipped_steps} "
                f"耗时={time.time() - start_time:.1f}s"
            )
            # 收尾已 record/refresh。partial 不当报错：正常 return（Celery SUCCESS），
            # Result 带 status=partial + 提示，便于在 flower 一眼区分「熔断未完成」与真报错(FAILURE)。
            return {
                "status": "partial",
                "progressed": progressed,
                "skipped": skipped_steps,
                "note": "步骤级熔断/未完成（预期，非报错）；已登记重试队列，后续多轮收敛",
            }
        finally:
            # 步骤5 释放写锁（无论成功失败）
            if write_lock is not None:
                await asyncio.to_thread(write_lock.release)

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except ReflectionRetriesExhausted:
        raise
    except ReflectionBusinessError as e:
        logger.error(f"反思高频do 业务失败 user={end_user_id}: {e}", exc_info=True)
        try:
            _rc = get_sync_redis_client()
            record_result = rr.record(
                _rc,
                "high_freq",
                end_user_id,
                "failed",
                progressed=False,
                skipped_steps=[e.failed_operation],
                reason_code=e.reason_code,
                model_type=e.model_type,
            )
            if record_result.outcome is rr.RetryRecordOutcome.EXHAUSTED:
                _report_reflection_failure(
                    task_type="high_freq",
                    end_user_id=end_user_id,
                    workspace_id=workspace_id,
                    reason_code=e.reason_code.value,
                    model_type=e.model_type.value,
                    failed_operations=[e.failed_operation],
                    last_failed_at_ms=record_result.last_failed_at_ms,
                )
        except Exception:
            logger.error("反思高频do 业务失败登记失败", exc_info=True)
        raise
    except Exception as e:
        # 真异常：run() 抛出未达 completion 逻辑，补登记 failed（无推进），再 re-raise（FAILURE + traceback，需排查）
        logger.error(f"反思高频do 失败 user={end_user_id}: {e}", exc_info=True)
        try:
            _rc = get_sync_redis_client()
            rr.record(
                _rc, "high_freq", end_user_id, "failed",
                progressed=False, last_error=str(e),
            )
        except Exception:
            pass
        raise
    finally:
        _shutdown_loop_gracefully(loop)
        # 步骤6 删除在途标记：放行下一轮 scan 对该用户的派发（成功/失败/跳过都要删）
        try:
            _rc = get_sync_redis_client()
            if _rc is not None:
                _rc.delete(inflight_key)
        except Exception:
            pass
    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    return result


@celery_app.task(
    name="app.tasks.scan_layer2_dedup_full_scan",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_layer2_dedup_full_scan(self) -> Dict[str, Any]:
    """低频去重扫描器：遍历用户，启用反思 + 最近活跃 + 未在途 的派发 do_layer2_dedup_full_scan。"""
    start_time = time.time()
    from app.models.workspace_model import Workspace

    redis_client = get_sync_redis_client()
    dispatched = 0
    dispatched_user_ids = []
    skip_inactive = 0
    skip_inflight = 0

    # db-session 规范：先用只读短 session 取 workspace 列表，
    # 再【按 workspace 粒度】开独立 session，处理完即释放，避免 identity-map 累积。
    with get_db_read() as db:
        workspace_ids = [str(w.id) for w in db.query(Workspace.id).all()]

    for ws_id in workspace_ids:
        ws_id_uuid = uuid.UUID(ws_id)
        with get_db_context() as db:
            memory_config_service = MemoryConfigService(db)
            try:
                config_id = memory_config_service.get_workspace_active_config_id(ws_id_uuid)
                config = memory_config_service.load_memory_config(config_id)
            except Exception as e:
                # 单个 workspace 配置异常（无启用配置 / 缺 embedding / 模型被删）只跳过该 workspace
                logger.warning(f"反思低频去重scan 跳过配置异常的 workspace={ws_id}: {e}")
                continue
            if not config.reflexion_enabled:
                continue
            for user in get_end_users_by_workspace(db, ws_id_uuid):
                uid = str(user.id)
                try:
                    # 最近活跃度过滤（复用现有函数，阈值取 settings）
                    if _should_skip_reflection_by_inactivity(db, uid):
                        skip_inactive += 1
                        continue
                    # 在途锁：抢不到说明该用户已有去重任务在途，跳过（独立 key）
                    if redis_client is not None:
                        ok = redis_client.set(
                            f"dedup:inflight:{uid}", "1", nx=True, ex=1500,
                        )
                        if not ok:
                            skip_inflight += 1
                            continue
                    do_layer2_dedup_full_scan.apply_async(
                        kwargs={
                            "end_user_id": uid,
                            "config_id": str(config_id),
                            "workspace_id": ws_id,
                        },
                        queue="reflection_tasks",
                    )
                    dispatched += 1
                    dispatched_user_ids.append(uid)
                except Exception as e:
                    logger.error(f"反思低频去重scan 处理用户失败 user={uid}: {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass

    logger.info(
        f"scan_layer2_dedup_full_scan 完成: 派发 {dispatched} {dispatched_user_ids}, "
        f"跳过(不活跃) {skip_inactive}, 在途 {skip_inflight}, "
        f"耗时 {time.time() - start_time:.1f}s"
    )
    return {"status": "SUCCESS", "dispatched": dispatched,
            "dispatched_user_ids": dispatched_user_ids,
            "skip_inactive": skip_inactive, "skip_inflight": skip_inflight}


@celery_app.task(
    name="app.tasks.do_layer2_dedup_full_scan",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def do_layer2_dedup_full_scan(self, end_user_id: str | None = None, config_id: str = "",
                              workspace_id: str = "", from_retry: bool = False,
                              user_id: str | None = None) -> Dict[str, Any]:
    """对【单个用户】执行一次低频全量去重扫描。

    由 scan_layer2_dedup_full_scan 派发。精确的增量判断在 run_dedup_full_scan 内部
    （check_new_entities 按实体类型查 Neo4j 新增数），do 这层不重复做。
    返回 status：success / lock_timeout / failed。
    """
    # HACK: 兼容旧参数 user_id，v0.3.15 后移除
    end_user_id = end_user_id or user_id
    if not end_user_id:
        raise ValueError("end_user_id is required")

    start_time = time.time()
    inflight_key = f"dedup:inflight:{end_user_id}"

    async def _run() -> Dict[str, Any]:
        from app.core.memory.memory_service import MemoryService

        # 抢该用户写锁：与反思 do、写入 pipeline 互斥。
        # 去重低频、半夜跑，给更长抢锁等待（120s），避免被高频反思挤掉；抢不到本次放弃。
        write_lock = None
        redis_client = get_sync_redis_client()
        if redis_client is not None:
            write_lock = RedisFairLock(
                key=f"memory_write:{end_user_id}",
                redis_client=redis_client,
                expire=600, timeout=120, auto_renewal=True,
            )
            if not await asyncio.to_thread(write_lock.acquire):
                logger.warning(f"反思低频去重do 获取写锁超时，跳过 user={end_user_id}")
                return {"status": "lock_timeout"}
        try:
            _rc = get_sync_redis_client()
            rr.lease(_rc, "dedup", end_user_id,
                     {"config_id": config_id, "workspace_id": workspace_id},
                     from_retry=from_retry)

            memory_service = MemoryService(
                config_id=uuid.UUID(config_id),
                end_user_id=end_user_id,
                workspace_id=workspace_id,
            )
            r = await memory_service.run_dedup_full_scan()
            completion = rr.completion_of_dedup(r)
            progressed = rr.progressed_dedup(r)
            merged = r.get("merged_count", 0)
            primary_reason = rr.select_primary_reason(rr.reason_codes_of_dedup(r))
            primary_model_type = rr.select_primary_model_type(
                rr.reason_codes_of_dedup(r), rr.model_types_of_dedup(r), primary_reason
            )

            if completion == "full":
                rr.resolve(_rc, "dedup", end_user_id)
                logger.info(
                    f"反思低频去重do 完成 user={end_user_id} status=success "
                    f"扫描类型={r.get('scanned_types', 0)} 合并={merged} "
                    f"耗时={time.time() - start_time:.1f}s"
                )
                return {"status": "success", "merged_count": merged}

            # partial：truncated / had_type_error。低频不刷 reflection_time（靠 update_scan_time 续扫）。
            record_result = rr.record(
                _rc,
                "dedup",
                end_user_id,
                completion,
                progressed,
                reason_code=primary_reason,
                model_type=primary_model_type,
            )
            if record_result.outcome is rr.RetryRecordOutcome.EXHAUSTED:
                _report_reflection_failure(
                    task_type="dedup",
                    end_user_id=end_user_id,
                    workspace_id=workspace_id,
                    reason_code=primary_reason,
                    model_type=primary_model_type,
                    failed_operations=[
                        str(r["failed_operation"])
                    ] if r.get("failed_operation") else [],
                    last_failed_at_ms=record_result.last_failed_at_ms,
                )
                if primary_reason is not None:
                    raise ReflectionRetriesExhausted(
                        ReflectionFailureReason(primary_reason)
                    )
            logger.warning(
                f"反思低频去重do 未完整完成 user={end_user_id} completion={completion} "
                f"progressed={progressed} truncated={r.get('truncated')} "
                f"had_type_error={r.get('had_type_error')} 合并={merged} "
                f"耗时={time.time() - start_time:.1f}s"
            )
            # partial 不当报错：正常 return（Celery SUCCESS），Result 带 status=partial + 提示。
            return {
                "status": "partial",
                "progressed": progressed,
                "merged_count": merged,
                "truncated": bool(r.get("truncated")),
                "had_type_error": bool(r.get("had_type_error")),
                "note": "低频去重未扫完（预期，非报错）；已登记重试队列，后续多轮收敛",
            }
        finally:
            if write_lock is not None:
                await asyncio.to_thread(write_lock.release)

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except ReflectionRetriesExhausted:
        raise
    except ReflectionBusinessError as e:
        logger.error(f"反思低频去重do 业务失败 user={end_user_id}: {e}", exc_info=True)
        try:
            _rc = get_sync_redis_client()
            record_result = rr.record(
                _rc,
                "dedup",
                end_user_id,
                "failed",
                progressed=False,
                skipped_steps=[e.failed_operation],
                reason_code=e.reason_code,
                model_type=e.model_type,
            )
            if record_result.outcome is rr.RetryRecordOutcome.EXHAUSTED:
                _report_reflection_failure(
                    task_type="dedup",
                    end_user_id=end_user_id,
                    workspace_id=workspace_id,
                    reason_code=e.reason_code.value,
                    model_type=e.model_type.value,
                    failed_operations=[e.failed_operation],
                    last_failed_at_ms=record_result.last_failed_at_ms,
                )
        except Exception:
            logger.error("反思低频去重业务失败登记失败", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"反思低频去重do 失败 user={end_user_id}: {e}", exc_info=True)
        try:
            _rc = get_sync_redis_client()
            rr.record(
                _rc, "dedup", end_user_id, "failed",
                progressed=False, last_error=str(e),
            )
        except Exception:
            pass
        raise
    finally:
        _shutdown_loop_gracefully(loop)
        # 删除在途标记：放行下一轮 scan 对该用户的派发（成功/失败都删）
        try:
            _rc = get_sync_redis_client()
            if _rc is not None:
                _rc.delete(inflight_key)
        except Exception:
            pass
    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    return result


@celery_app.task(
    name="app.tasks.scan_reflection_retry",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_reflection_retry(self) -> Dict[str, Any]:
    """重试派发：扫两个重试 ZSet，对「已到点」用户绕活跃闸门重派对应 do。

    租约到期且仍 in_progress → 判进程死亡 mark_dead；meta 缺失的孤儿 member → zrem 清理；
    仍走 inflight 锁与正常 scan 互斥去重；派发的 do 进 reflection_tasks 队列（与正常 scan 同队列）。
    Redis 不可用时整轮 no-op，不影响反思主流程。
    """
    start_time = time.time()
    rc = get_sync_redis_client()
    if rc is None:
        logger.warning("scan_reflection_retry: Redis 不可用，跳过本轮")
        return {"status": "SKIPPED", "reason": "redis_unavailable"}

    now = time.time()
    batch = rr.RETRY_BATCH
    dispatched = 0
    cleaned = 0
    dispatched_uids: Dict[str, List[str]] = {"high_freq": [], "dedup": []}

    for task_type, do_task, inflight_prefix in (
            ("high_freq", do_layer2_reflection, "reflection:inflight"),
            ("dedup", do_layer2_dedup_full_scan, "dedup:inflight"),
    ):
        zkey = f"reflection:retry:{task_type}"
        try:
            uids = rc.zrangebyscore(zkey, "-inf", now, start=0, num=batch)
        except Exception as e:
            logger.warning(f"scan_reflection_retry: zrangebyscore 失败 {zkey}: {e}")
            continue
        for uid in uids:
            if isinstance(uid, bytes):
                uid = uid.decode("utf-8")
            try:
                meta = rr.load_meta(rc, task_type, uid)
                if not meta:
                    rc.zrem(zkey, uid)  # 孤儿（meta 已 TTL 过期）→ 清理
                    cleaned += 1
                    continue
                if meta.get("completion") == "exhausted":
                    continue
                if meta.get("completion") == "in_progress":  # 租约到期 = 上次开工后进程死亡
                    if not rr.mark_dead(rc, task_type, uid):
                        continue
                # 仍走 inflight 锁，避免与正常 scan 派的同一用户撞车
                if not rc.set(f"{inflight_prefix}:{uid}", "1", nx=True, ex=1500):
                    continue
                kwargs = {"end_user_id": uid, "config_id": meta["config_id"],
                          "workspace_id": meta["workspace_id"], "from_retry": True}
                if task_type == "high_freq":
                    kwargs["iteration_period"] = meta.get("iteration_period", 24)
                do_task.apply_async(kwargs=kwargs, queue="reflection_tasks")
                dispatched += 1
                dispatched_uids[task_type].append(uid)
            except Exception as e:
                logger.error(f"scan_reflection_retry 处理用户失败 task_type={task_type} uid={uid}: {e}")

    logger.info(f"scan_reflection_retry 完成: 派发 {dispatched}, 清理孤儿 {cleaned}, "
                f"耗时 {time.time() - start_time:.1f}s")
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "cleaned": cleaned,
        "dispatched_uids": dispatched_uids,
    }


# =============================================================================
# GDS 拓扑分数（eigenvector 中心性）：扫描活跃用户 → heavy 计算
# =============================================================================


@celery_app.task(
    name="app.tasks.scan_gds_topology_score",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_gds_topology_score(self) -> Dict[str, Any]:
    start_time = time.time()

    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("scan_gds_topology_score 终止：Redis 不可用，拒绝无锁派发")
        raise RuntimeError("Redis unavailable: gds topology scan requires inflight locks")

    dispatched = 0
    dispatched_user_ids = []
    skip_inflight = 0

    with get_db_read() as db:
        workspace_ids = get_all_active_workspaces(db)

    for ws_id_uuid in workspace_ids:
        with get_db_read() as db:
            active_since = utcnow_naive() - timedelta(hours=settings.GDS_TOPOLOGY_ACTIVE_HOURS)
            for user in get_active_end_users_by_workspace(db, ws_id_uuid, active_since):
                uid = str(user.id)
                try:
                    inflight_token = uuid.uuid4().hex
                    ok = redis_client.set(
                        _GDS_TOPOLOGY_INFLIGHT_KEY_FMT.format(end_user_id=uid),
                        inflight_token, nx=True, ex=settings.GDS_TOPOLOGY_INFLIGHT_TTL_SEC,
                    )
                    if not ok:
                        skip_inflight += 1
                        continue
                    do_gds_topology_score.apply_async(
                        kwargs={"end_user_id": uid, "inflight_token": inflight_token},
                        queue="memory_heavy_tasks",
                    )
                    dispatched += 1
                    dispatched_user_ids.append(uid)
                    if dispatched % 10 == 0:
                        logger.info(
                            f"scan_gds_topology_score 进度: 已派发 {dispatched} 个用户, "
                            f"最近10个: {dispatched_user_ids[-10:]}"
                        )
                except Exception as e:
                    logger.error(f"GDS拓扑scan 处理用户失败 user={uid}: {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass

    logger.info(
        f"scan_gds_topology_score 完成: 派发 {dispatched} {dispatched_user_ids}, "
        f"在途 {skip_inflight}, "
        f"耗时 {time.time() - start_time:.1f}s"
    )
    return {"status": "SUCCESS", "dispatched": dispatched,
            "dispatched_user_ids": dispatched_user_ids,
            "skip_inflight": skip_inflight}


@celery_app.task(
    name="app.tasks.do_gds_topology_score",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def do_gds_topology_score(self, end_user_id: str, inflight_token: Optional[str] = None) -> Dict[str, Any]:
    if not end_user_id:
        raise ValueError("end_user_id is required")

    inflight_key = _GDS_TOPOLOGY_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
    start_time = time.time()

    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.warning(f"GDS拓扑do 跳过：Redis 不可用 user={end_user_id}")
        return {"status": "skipped_no_redis", "end_user_id": end_user_id}
    if not inflight_token:
        logger.warning(f"GDS拓扑do 跳过：缺少在途锁 token user={end_user_id}")
        return {"status": "skipped_no_token", "end_user_id": end_user_id}
    if redis_client.get(inflight_key) != inflight_token:
        logger.warning(f"GDS拓扑do 跳过：在途锁已失效（过期或被新一轮 scan 重设）user={end_user_id}")
        return {"status": "skipped_stale_inflight", "end_user_id": end_user_id}

    async def _run() -> Dict[str, Any]:
        from app.core.memory.storage.custom import compute_topology_score

        write_lock = RedisFairLock(
            key=f"memory_write:{end_user_id}",
            redis_client=redis_client,
            expire=600, timeout=30, auto_renewal=True,
        )
        if not await asyncio.to_thread(write_lock.acquire):
            logger.warning(f"GDS拓扑do 获取写锁超时，跳过 user={end_user_id}")
            return {"status": "lock_timeout"}
        try:
            return await compute_topology_score(end_user_id)
        finally:
            await asyncio.to_thread(write_lock.release)

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        result["end_user_id"] = end_user_id
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    except Exception as e:
        # GDS 投影 / eigenvector.write / drop 抛错，re-raise 让 Celery 标记 FAILURE（带 traceback）
        logger.error(f"GDS拓扑do 失败 user={end_user_id}: {e}", exc_info=True)
        raise
    finally:
        _shutdown_loop_gracefully(loop)
        try:
            redis_client.eval(UNLOCK_SCRIPT, 1, inflight_key, inflight_token)
        except Exception as e:
            logger.warning(f"GDS拓扑do 释放在途锁失败 user={end_user_id}: {e}")


@celery_app.task(
    name="app.tasks.sync_all_end_user_memory_counts",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False
)
def sync_all_end_user_memory_counts(self) -> Dict[str, Any]:
    """
    Operations manual tasks
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.core.memory.utils.memory_count_utils import (
            sync_end_user_memory_count_from_neo4j,
        )
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector

        # 只读短 session 枚举活跃用户 ID，随后立即关闭
        with get_db_read() as db:
            user_ids = [
                str(u.id)
                for u in db.query(EndUser)
                .filter(EndUser.is_active == True, EndUser.memory_count >= 300)
                .all()
            ]

        connector = Neo4jConnector()
        succeeded = 0
        failed = 0
        failed_ids: list[str] = []
        try:
            for uid in user_ids:
                try:
                    await sync_end_user_memory_count_from_neo4j(uid, connector)
                    succeeded += 1
                except Exception as e:
                    failed += 1
                    failed_ids.append(uid)
                    logger.warning(f"[MemoryCountSync] 同步失败 user={uid}: {e}")
        finally:
            await connector.close()

        return {
            "status": "SUCCESS",
            "total": len(user_ids),
            "succeeded": succeeded,
            "failed": failed,
            "failed_ids": failed_ids,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except Exception as e:
        logger.error(f"[MemoryCountSync] 全量同步异常: {e}", exc_info=True)
        raise
    finally:
        _shutdown_loop_gracefully(loop)

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    return result


# unused task
#     """Call read_service and write latest status to Redis.

#     Returns status data dict that gets written to Redis.
#     """
#     client = redis.Redis(
#         host=settings.REDIS_HOST,
#         port=settings.REDIS_PORT,
#         db=settings.REDIS_DB,
#         password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None
#     )
#     try:
#         api_url = f"http://{settings.SERVER_IP}:8000/api/memory/read_service"
#         payload = {
#             "user_id": "健康检查",
#             "apply_id": "健康检查",
#             "group_id": "健康检查",
#             "message": "你好",
#             "history": [],
#             "search_switch": "2",
#         }
#         resp = requests.post(api_url, json=payload, timeout=15)
#         ok = resp.status_code == 200
#         status = "Success" if ok else "Fail"
#         msg = "接口请求成功" if ok else f"接口请求失败: {resp.status_code}"
#         error = "" if ok else resp.text
#         code = 0 if ok else 500
#     except Exception as e:
#         status = "Fail"
#         msg = "接口请求失败"
#         error = str(e)
#         code = 500

#     data = {
#         "status": status,
#         "msg": msg,
#         "error": error,
#         "code": str(code),
#         "time": str(int(time.time())),
#     }

#     client.hset("memsci:health:read_service", mapping=data)
#     client.expire("memsci:health:read_service", int(settings.HEALTH_CHECK_SECONDS))

#     return data


@celery_app.task(name="app.tasks.write_total_memory_task")
def write_total_memory_task(workspace_id: str) -> Dict[str, Any]:
    """定时任务：查询工作空间下所有宿主的记忆总量并写入数据库

    记忆总量取自 end_users.memory_count 汇总（与记忆总量接口、全量统计任务同一聚合口径），
    不再扫描 Neo4j。

    Args:
        workspace_id: 工作空间ID

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    from app.repositories.end_user_repository import EndUserRepository
    from app.repositories.memory_increment_repository import MemoryIncrementRepository

    try:
        workspace_uuid = uuid.UUID(workspace_id)

        # --- 单 session：聚合活跃宿主记忆量 → 写入统计结果 ---
        with get_db_context() as db:
            # 无活跃宿主时不在返回中，按 (0, 0) 处理
            total_num, end_user_count = (
                EndUserRepository(db)
                .get_memory_count_stats_by_workspace_ids([workspace_uuid])
                .get(workspace_uuid, (0, 0))
            )

            # 返回值即客户端生成的内存增量主键与时间戳，无需再访问已过期的 ORM 属性
            increment_id, increment_created_at = MemoryIncrementRepository(db).write_memory_increment(
                workspace_id=workspace_uuid,
                total_num=total_num
            )

        return {
            "status": "SUCCESS",
            "workspace_id": workspace_id,
            "total_num": total_num,
            "end_user_count": end_user_count,
            "memory_increment_id": str(increment_id),
            "created_at": to_iso_z(increment_created_at),
            "elapsed_time": time.time() - start_time,
        }
    except Exception as e:
        return {
            "status": "FAILURE",
            "error": str(e),
            "workspace_id": workspace_id,
            "elapsed_time": time.time() - start_time,
        }


@celery_app.task(
    name="app.tasks.write_all_workspaces_memory_task",
    bind=True,
    ignore_result=False,
    max_retries=3,
    acks_late=True,
    time_limit=3600,
    soft_time_limit=3300,
)
def write_all_workspaces_memory_task(self) -> Dict[str, Any]:
    """定时任务：遍历所有工作空间，统计并写入记忆增量

    此任务会：
    1. 查询所有活跃的工作空间
    2. 汇总每个工作空间活跃宿主的 memory_count 作为记忆总量
    3. 将统计结果写入 memory_increments 表

    改造说明：记忆总量改为聚合 end_users.memory_count（与记忆总量接口同源），
    不再逐空间扫描 Neo4j，避免全图扫描与结果集全量物化带来的耗时与内存开销。

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    from app.models.workspace_model import Workspace
    from app.repositories.end_user_repository import EndUserRepository
    from app.repositories.memory_increment_repository import MemoryIncrementRepository

    try:
        # --- 短 session：获取活跃 workspace 列表 + 记忆总量聚合，随后立即关闭 ---
        with get_db_context() as db:
            workspaces = db.query(Workspace.id, Workspace.name).filter(
                Workspace.is_active.is_(True)
            ).all()
            workspace_list = [{"id": workspace.id, "name": workspace.name} for workspace in workspaces]

            # {workspace_id: (memory_total, host_count)}；列表为空时仓库侧短路返回 {}
            stats_by_workspace: dict = EndUserRepository(db).get_memory_count_stats_by_workspace_ids(
                [workspace_info["id"] for workspace_info in workspace_list]
            )

        if not workspace_list:
            logger.warning("没有找到活跃的工作空间")
            return {
                "status": "SUCCESS",
                "message": "没有找到活跃的工作空间",
                "workspace_count": 0,
                "workspace_results": [],
                "elapsed_time": time.time() - start_time,
                "task_id": self.request.id,
            }

        logger.info(f"开始统计 {len(workspace_list)} 个工作空间的记忆增量")
        results: list[dict] = []

        # 逐 workspace 处理，每轮独立短 session
        for workspace_info in workspace_list:
            workspace_id = workspace_info["id"]
            workspace_name = workspace_info["name"]

            try:
                logger.info(f"开始处理工作空间: {workspace_name} (ID: {workspace_id})")

                # 无活跃宿主时按 0 处理
                total_num, end_user_count = stats_by_workspace.get(workspace_id, (0, 0))

                # --- Session：写入统计结果 ---
                # 每个 workspace 独立 session：单空间写入失败不会污染 session、影响其余空间
                with get_db_context() as db:
                    # 返回值即客户端生成的内存增量主键与时间戳，无需再访问已过期的 ORM 属性
                    increment_id, increment_created_at = MemoryIncrementRepository(db).write_memory_increment(
                        workspace_id=workspace_id,
                        total_num=total_num,
                    )

                results.append({
                    "workspace_id": str(workspace_id),
                    "workspace_name": workspace_name,
                    "status": "SUCCESS",
                    "total_num": total_num,
                    "end_user_count": end_user_count,
                    "memory_increment_id": str(increment_id),
                    "created_at": to_iso_z(increment_created_at),
                })
                logger.info(
                    f"工作空间 {workspace_name} 统计完成: 总量={total_num}, 用户数={end_user_count}"
                )

            except Exception as e:
                # 单 workspace 失败不影响其他 workspace
                logger.error(f"处理工作空间 {workspace_name} (ID: {workspace_id}) 失败: {e}")
                results.append({
                    "workspace_id": str(workspace_id),
                    "workspace_name": workspace_name,
                    "status": "FAILURE",
                    "error": str(e),
                    "total_num": 0,
                    "end_user_count": 0,
                })

        total_memory = sum(r.get("total_num", 0) for r in results)
        success_count = sum(1 for r in results if r["status"] == "SUCCESS")

        return {
            "status": "SUCCESS",
            "message": f"成功处理 {success_count}/{len(workspace_list)} 个工作空间，总记忆量: {total_memory}",
            "workspace_count": len(workspace_list),
            "success_count": success_count,
            "total_memory": total_memory,
            "workspace_results": results,
            "elapsed_time": time.time() - start_time,
            "task_id": self.request.id,
        }
    except Exception as e:
        return {
            "status": "FAILURE",
            "error": str(e),
            "elapsed_time": time.time() - start_time,
            "task_id": self.request.id
        }


# ============================================================
# 洞察/摘要缓存刷新：扫描 + 派发模式（替代旧 refresh_memory_insight_and_summary_cache 单任务）
# ============================================================

# 在途锁 key 与 TTL：TTL 略大于 do 任务的 time_limit(900s)，兜底 worker 崩溃不会永久占用
CACHE_INFLIGHT_KEY_FMT = "insight_summary_cache:inflight:{end_user_id}"
CACHE_INFLIGHT_TTL_SEC = 1800


@celery_app.task(
    name="app.tasks.scan_refresh_insight_summary_cache",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,  # 10 分钟硬超时（仅枚举 + 派发，足够）
    soft_time_limit=540,
)
def scan_refresh_insight_summary_cache(self) -> Dict[str, Any]:
    """扫描原始刷新字段，并派发需要更新洞察或摘要的用户。"""
    start_time = time.time()
    from app.core.memory.analytics.memory_insight import classify_memory_cache_refresh
    from app.repositories.end_user_repository import EndUserRepository

    redis_client = get_sync_redis_client()
    dispatched = 0
    dispatched_user_ids: List[str] = []
    skip_no_change = 0  # write_time 为 null 或 数据未变
    skip_fresh = 0  # 数据有变但缓存刚刷过（未到最短刷新间隔）
    skip_inflight = 0  # 在途锁未抢到

    # db-session 规范：先用只读短 session 取 workspace 列表，
    # 再【按 workspace 粒度】开独立 session，处理完即释放，避免 identity-map 累积。
    with get_db_read() as db:
        workspace_ids = EndUserRepository(db).get_all_active_workspaces()

    for ws_id in workspace_ids:
        # 列裁剪查询：返回普通元组，不受 session 关闭后 detach 影响，且内存更省
        with get_db_read() as db:
            rows = EndUserRepository(db).get_neo4j_memory_cache_refresh_fields(ws_id)

        for row in rows:
            eu_id = str(row.end_user_id)
            try:
                decisions = classify_memory_cache_refresh(
                    insight_at=row.memory_insight_updated_at,
                    summary_at=row.user_summary_updated_at,
                    write_at=row.write_time,
                    metadata_updated_at=row.metadata_updated_at,
                )
                refresh_insight = decisions.insight == "dispatch"
                refresh_summary = decisions.summary == "dispatch"
                if not refresh_insight and not refresh_summary:
                    if "skip_fresh" in (decisions.insight, decisions.summary):
                        skip_fresh += 1
                    else:
                        skip_no_change += 1
                    continue

                # 在途锁：抢不到说明该用户已有刷新任务在途，跳过
                if redis_client is not None:
                    ok = redis_client.set(
                        CACHE_INFLIGHT_KEY_FMT.format(end_user_id=eu_id),
                        "1", nx=True, ex=CACHE_INFLIGHT_TTL_SEC,
                    )
                    if not ok:
                        skip_inflight += 1
                        continue

                # 派发：用 countdown 错峰，每 60 个一波、每波摊到 0~295s，平滑 LLM 调用
                countdown = (dispatched % 60) * 5
                do_refresh_insight_summary_cache.apply_async(
                    kwargs={
                        "end_user_id": eu_id,
                        "workspace_id": str(row.workspace_id),
                        "language": "zh",  # 与旧任务行为对齐
                        "refresh_insight": refresh_insight,
                        "refresh_summary": refresh_summary,
                    },
                    countdown=countdown,
                )
                dispatched += 1
                dispatched_user_ids.append(eu_id)
                if dispatched % 50 == 0:
                    logger.info(
                        f"scan_refresh_insight_summary_cache 进度: 已派发 {dispatched}, "
                        f"最近 10 个: {dispatched_user_ids[-10:]}"
                    )
            except Exception as e:
                logger.error(f"洞察/摘要缓存scan 处理用户失败 user={eu_id}: {e}")

    logger.info(
        f"scan_refresh_insight_summary_cache 完成: 派发 {dispatched}, "
        f"跳过(数据未变) {skip_no_change}, 跳过(刚刷过) {skip_fresh}, "
        f"跳过(在途) {skip_inflight}, 耗时 {time.time() - start_time:.1f}s"
    )
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "dispatched_user_ids": dispatched_user_ids,
        "skip_no_change": skip_no_change,
        "skip_fresh": skip_fresh,
        "skip_inflight": skip_inflight,
        "elapsed_time": time.time() - start_time,
        "task_id": self.request.id,
    }


@celery_app.task(
    name="app.tasks.do_refresh_insight_summary_cache",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=900,  # 15 分钟硬超时
    soft_time_limit=840,  # 14 分钟软超时
)
def do_refresh_insight_summary_cache(
        self,
        end_user_id: str,
        workspace_id: str,
        language: str = "zh",
        refresh_insight: bool = True,
        refresh_summary: bool = True,
) -> Dict[str, Any]:
    """按 scan 的独立判定刷新单个用户的记忆洞察或用户摘要缓存。

    由 scan_refresh_insight_summary_cache 派发，每个用户一个独立任务；PostgreSQL
    读写使用独立同步短 Session，Neo4j/LLM 保持异步。刷新标记默认开启，
    兼容发布前已入队的旧消息。
    """
    start_time = time.time()
    inflight_key = CACHE_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)

    async def _run() -> Dict[str, Any]:
        from app.services.user_memory_service import UserMemoryService

        service = UserMemoryService()
        ws_uuid = uuid.UUID(workspace_id)
        insight = None
        summary = None

        # 旧 Celery 异步 PG 编排保留如下：
        # async with get_async_db_context() as db:
        #     insight = await service.generate_and_cache_insight(db, ...)
        #     summary = await service.generate_and_cache_summary(db, ...)
        # 模块级 asyncpg pool 会跨 Task/event loop 复用连接，存在
        # "Future attached to a different loop" 和连接协议状态损坏风险。
        if refresh_insight:
            insight = await service.generate_and_cache_insight_for_worker(
                end_user_id,
                ws_uuid,
                language=language,
            )
        if refresh_summary:
            summary = await service.generate_and_cache_summary_for_worker(
                end_user_id,
                ws_uuid,
                language=language,
            )

        insight_success = bool(insight and insight.get("success"))
        summary_success = bool(summary and summary.get("success"))
        return {
            "insight_success": insight_success if refresh_insight else None,
            "summary_success": summary_success if refresh_summary else None,
            "insight_status": (
                "success" if insight_success else "failed"
            ) if refresh_insight else "skipped",
            "summary_status": (
                "success" if summary_success else "failed"
            ) if refresh_summary else "skipped",
            "insight_error": insight.get("error") if insight else None,
            "summary_error": summary.get("error") if summary else None,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        requested_successes = []
        if refresh_insight:
            requested_successes.append(result["insight_success"])
        if refresh_summary:
            requested_successes.append(result["summary_success"])
        if requested_successes and not any(requested_successes):
            raise RuntimeError(
                f"all requested cache refreshes failed: "
                f"insight_error={result.get('insight_error')}, "
                f"summary_error={result.get('summary_error')}"
            )
        if not requested_successes:
            result["status"] = "skipped"
        elif all(requested_successes):
            result["status"] = "success"
        else:
            result["status"] = "partial"
        logger.info(
            f"do_refresh_insight_summary_cache 完成 user={end_user_id} status={result['status']} "
            f"insight={result['insight_status']} summary={result['summary_status']} "
            f"耗时={time.time() - start_time:.1f}s"
        )
    # 异常不再 catch，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)
        # 删除在途标记：放行下一轮 scan 对该用户的派发。
        try:
            _rc = get_sync_redis_client()
            if _rc is not None:
                _rc.delete(inflight_key)
        except Exception:
            pass

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    result["end_user_id"] = end_user_id
    return result


# 用户名片 Tag 定时刷新任务

USER_TAG_INFLIGHT_KEY_FMT = "user_tags:inflight:{end_user_id}"
USER_TAG_INFLIGHT_TTL_SEC = 600
USER_TAG_SCAN_PAGE_SIZE = 500


@celery_app.task(
    name="app.tasks.scan_refresh_user_tags",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def scan_refresh_user_tags(self) -> Dict[str, Any]:
    """分页扫描待刷新用户，并为每个用户派发独立的 Tag 刷新任务。

    扫描任务只负责筛选和派发，不读取完整 metadata，也不调用 LLM，避免一个长任务持续
    占用数据库连接。实际生成由 ``do_refresh_user_tags`` 在 heavy worker 中完成。
    """
    from app.repositories.end_user_repository import EndUserRepository

    start_time = time.time()
    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("用户名片Tag scan终止：Redis客户端不可用，拒绝无锁派发")
        raise RuntimeError("Redis unavailable: user tag scan requires inflight locks")

    after_id: uuid.UUID | None = None
    candidates_count = 0
    dispatched = 0
    skip_inflight = 0
    failed = 0

    while True:
        with get_db_read() as db:
            candidates = EndUserRepository(db).get_user_tag_refresh_candidates(
                after_id=after_id,
                limit=USER_TAG_SCAN_PAGE_SIZE,
            )
        if not candidates:
            break

        candidates_count += len(candidates)
        for candidate in candidates:
            end_user_id = str(candidate.end_user_id)
            inflight_key = USER_TAG_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
            try:
                # Redis 在途标记防止相邻两轮扫描为同一用户重复派发任务。
                lock_acquired = bool(
                    redis_client.set(
                        inflight_key,
                        "1",
                        nx=True,
                        ex=USER_TAG_INFLIGHT_TTL_SEC,
                    )
                )
            except Exception as exc:
                logger.error(
                    "用户名片Tag scan终止：Redis在途锁不可用 user=%s error=%s",
                    end_user_id,
                    str(exc),
                    exc_info=True,
                )
                raise RuntimeError("Redis unavailable: failed to acquire user tag inflight lock") from exc

            if not lock_acquired:
                skip_inflight += 1
                continue

            try:
                # 每 60 个任务分散到 5 分钟内启动，削平 LLM 和数据库的瞬时压力。
                countdown = (dispatched % 60) * 5
                do_refresh_user_tags.apply_async(
                    kwargs={
                        "end_user_id": end_user_id,
                        "workspace_id": str(candidate.workspace_id),
                    },
                    countdown=countdown,
                    queue="memory_heavy_tasks",
                )
                dispatched += 1
            except Exception as exc:
                failed += 1
                logger.error(
                    "用户名片Tag scan派发失败 user=%s error=%s",
                    end_user_id,
                    str(exc),
                    exc_info=True,
                )
                try:
                    redis_client.delete(inflight_key)
                except Exception:
                    logger.warning("用户名片Tag scan回滚在途锁失败 user=%s", end_user_id, exc_info=True)

        after_id = candidates[-1].end_user_id
        if len(candidates) < USER_TAG_SCAN_PAGE_SIZE:
            break

    result = {
        "status": "SUCCESS",
        "candidates": candidates_count,
        "dispatched": dispatched,
        "skip_inflight": skip_inflight,
        "failed": failed,
        "elapsed_time": time.time() - start_time,
        "task_id": self.request.id,
    }
    logger.info(
        "scan_refresh_user_tags完成 candidates=%s dispatched=%s skip_inflight=%s failed=%s",
        candidates_count,
        dispatched,
        skip_inflight,
        failed,
    )
    return result


@celery_app.task(
    name="app.tasks.do_refresh_user_tags",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=120,
    soft_time_limit=90,
)
def do_refresh_user_tags(
        self,
        end_user_id: str,
        workspace_id: str,
) -> Dict[str, Any]:
    """在 heavy worker 中调用记忆领域入口，刷新单个用户的名片 Tag。

    Celery 任务本身是同步函数，新事件循环只用于驱动异步 LLM 调用；领域层中的 PostgreSQL
    操作仍使用同步短会话，并且不会在等待 LLM 时持有数据库连接。
    """
    inflight_key = USER_TAG_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.core.memory.memory_service import MemoryService

        return await MemoryService.refresh_user_card_tags(end_user_id, workspace_id)

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    finally:
        _shutdown_loop_gracefully(loop)
        # 无论任务成功还是异常都释放在途标记，让后续扫描可以再次处理该用户。
        try:
            redis_client = get_sync_redis_client()
            if redis_client is not None:
                redis_client.delete(inflight_key)
        except Exception:
            logger.warning("用户名片Tag do释放在途锁失败 user=%s", end_user_id, exc_info=True)

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    result["end_user_id"] = end_user_id
    logger.info("do_refresh_user_tags完成 user=%s status=%s", end_user_id, result["status"])
    return result


# @celery_app.task(
#     name="app.tasks.run_forgetting_cycle_task",
#     bind=True,
#     ignore_result=False,  # 改为 False 以便在 Flower 中查看结果
#     max_retries=0,
#     acks_late=False,
#     time_limit=7200,
#     soft_time_limit=7000,
# )
# def run_forgetting_cycle_task(self, config_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
#     """定时任务：运行遗忘周期

#     遍历所有终端用户，执行遗忘周期。
#     """
#     start_time = time.time()

#     async def _process_users() -> Dict[str, Any]:
#         from app.repositories.end_user_repository import EndUserRepository
#         with get_db_context() as db:
#             end_users = EndUserRepository(db).get_all_active()
#             if not end_users:
#                 logger.info("没有终端用户，跳过遗忘周期")
#                 return {"status": "SUCCESS", "message": "没有终端用户",
#                         "report": {"merged_count": 0, "failed_count": 0, "processed_users": 0},
#                         "duration_seconds": time.time() - start_time}

#             logger.info(f"开始处理 {len(end_users)} 个终端用户的遗忘周期")
#             forget_service = MemoryForgetService()
#             total_merged = total_failed = processed_users = 0
#             failed_users = []

#             for end_user in end_users:
#                 try:
#                     config_id = MemoryConfigService(db).get_workspace_active_config_id(end_user.workspace_id)

#                     # 执行遗忘周期
#                     report = await forget_service.trigger_forgetting_cycle(
#                         db=db, end_user_id=str(end_user.id), config_id=config_id
#                     )

#                     total_merged += report.get('merged_count', 0)
#                     total_failed += report.get('failed_count', 0)
#                     processed_users += 1

#                     logger.info(f"用户 {end_user.id}: 融合 {report.get('merged_count', 0)} 对节点")

#                 except Exception as e:
#                     logger.error(f"处理用户 {end_user.id} 失败: {e}", exc_info=True)
#                     failed_users.append({"end_user_id": str(end_user.id), "error": str(e)})

#             duration = time.time() - start_time
#             logger.info(f"遗忘周期完成: {processed_users}/{len(end_users)} 用户, "
#                         f"融合 {total_merged} 对, 耗时 {duration:.2f}s")

#             return {
#                 "status": "SUCCESS",
#                 "message": f"处理 {processed_users} 个用户",
#                 "report": {
#                     "merged_count": total_merged,
#                     "failed_count": total_failed,
#                     "processed_users": processed_users,
#                     "total_users": len(end_users),
#                     "failed_users": failed_users
#                 },
#                 "duration_seconds": duration
#             }

#     # 直接运行异步函数，全局异常自然冒出 → Celery FAILURE；
#     # 内层逐用户 try/except 已在 _process_users 中隔离单用户失败。
#     # asyncio.run 自行管理 event loop 生命周期，无需手动清理。
#     result = asyncio.run(_process_users())
#     result["elapsed_time"] = time.time() - start_time
#     result["task_id"] = self.request.id
#     return result


_SOFT_DELETE_INFLIGHT_PREFIX = "soft_delete:inflight:"
_SOFT_DELETE_INFLIGHT_TTL_SECONDS = 86400
_SOFT_DELETE_BATCH_SIZE = 100


def _soft_delete_inflight_key(end_user_id: str) -> str:
    return f"{_SOFT_DELETE_INFLIGHT_PREFIX}{end_user_id}"


def _release_soft_delete_inflight(
        redis_client: Optional[redis.StrictRedis],
        end_user_id: str,
        batch_id: str,
) -> None:
    """仅在锁仍属于当前批次时释放，避免误删后续批次的锁。"""
    if redis_client is None:
        return
    try:
        redis_client.eval(
            UNLOCK_SCRIPT,
            1,
            _soft_delete_inflight_key(end_user_id),
            batch_id,
        )
    except RedisError as e:
        logger.warning(
            f"[ExpiredEndUser] 释放 inflight 锁失败: end_user_id={end_user_id}, error={e}"
        )


@celery_app.task(
    name="app.tasks.scan_expired_end_users",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    soft_time_limit=540,
    time_limit=600,
)
def scan_expired_end_users(self) -> Dict[str, Any]:
    """扫描全部过期临时身份，并按每批最多 100 个派发清理任务。"""
    from app.repositories.end_user_repository import EndUserRepository

    started_at = time.time()
    batch_id = self.request.id or uuid.uuid4().hex

    try:
        with get_db_context() as db:
            candidates = EndUserRepository(db).get_expired_temporary_end_user_ids()
    except Exception as e:
        logger.error(f"[ExpiredEndUserScan] 查询失败: {e}", exc_info=True)
        return {"status": "FAILED", "reason": "query_failed", "error": str(e)}

    if not candidates:
        return {
            "status": "SUCCESS",
            "batch_id": batch_id,
            "candidates": 0,
            "locked": 0,
            "lock_conflicts": 0,
            "dispatched": 0,
        }

    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("[ExpiredEndUserScan] Redis 不可用，取消派发")
        return {
            "status": "FAILED",
            "reason": "redis_unavailable",
            "batch_id": batch_id,
            "candidates": len(candidates),
            "dispatched": 0,
        }

    locked_count = 0
    lock_conflicts = 0
    dispatched_count = 0
    dispatched_batches = 0
    for batch_start in range(0, len(candidates), _SOFT_DELETE_BATCH_SIZE):
        candidate_batch = candidates[
            batch_start:batch_start + _SOFT_DELETE_BATCH_SIZE
        ]
        locked_ids: List[str] = []
        try:
            for candidate_id in candidate_batch:
                end_user_id = str(candidate_id)
                acquired = redis_client.set(
                    _soft_delete_inflight_key(end_user_id),
                    batch_id,
                    nx=True,
                    ex=_SOFT_DELETE_INFLIGHT_TTL_SECONDS,
                )
                if acquired:
                    locked_ids.append(end_user_id)
                    locked_count += 1
                else:
                    lock_conflicts += 1
        except RedisError as e:
            for end_user_id in locked_ids:
                _release_soft_delete_inflight(redis_client, end_user_id, batch_id)
            locked_count -= len(locked_ids)
            logger.error(f"[ExpiredEndUserScan] Redis 加锁失败: {e}", exc_info=True)
            return {
                "status": "PARTIAL_FAILURE" if dispatched_count else "FAILED",
                "reason": "redis_lock_failed",
                "batch_id": batch_id,
                "candidates": len(candidates),
                "locked": locked_count,
                "lock_conflicts": lock_conflicts,
                "dispatched": dispatched_count,
                "dispatched_batches": dispatched_batches,
            }

        if not locked_ids:
            continue

        try:
            do_soft_delete_end_users.apply_async(
                kwargs={"end_user_ids": locked_ids, "batch_id": batch_id},
                queue="memory_heavy_tasks",
            )
        except Exception as e:
            for end_user_id in locked_ids:
                _release_soft_delete_inflight(redis_client, end_user_id, batch_id)
            locked_count -= len(locked_ids)
            logger.error(f"[ExpiredEndUserScan] 派发失败: {e}", exc_info=True)
            return {
                "status": "PARTIAL_FAILURE" if dispatched_count else "FAILED",
                "reason": "dispatch_failed",
                "batch_id": batch_id,
                "candidates": len(candidates),
                "locked": locked_count,
                "lock_conflicts": lock_conflicts,
                "dispatched": dispatched_count,
                "dispatched_batches": dispatched_batches,
            }
        dispatched_count += len(locked_ids)
        dispatched_batches += 1

    elapsed = time.time() - started_at
    logger.info(
        f"[ExpiredEndUserScan] 完成: batch_id={batch_id}, "
        f"candidates={len(candidates)}, locked={locked_count}, "
        f"lock_conflicts={lock_conflicts}, dispatched={dispatched_count}, "
        f"batches={dispatched_batches}, elapsed={elapsed:.1f}s"
    )
    return {
        "status": "SUCCESS",
        "batch_id": batch_id,
        "candidates": len(candidates),
        "locked": locked_count,
        "lock_conflicts": lock_conflicts,
        "dispatched": dispatched_count,
        "dispatched_batches": dispatched_batches,
        "elapsed_time": elapsed,
    }


@celery_app.task(
    name="app.tasks.do_soft_delete_end_users",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    soft_time_limit=1080,
    time_limit=1200,
)
def do_soft_delete_end_users(
        self,
        end_user_ids: List[str],
        batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """批量软删过期临时身份，逐用户隔离错误并兜底释放 inflight 锁。"""
    from app.repositories.end_user_repository import EndUserRepository

    started_at = time.time()
    effective_batch_id = batch_id or self.request.id or uuid.uuid4().hex
    unique_ids = list(dict.fromkeys(str(item) for item in (end_user_ids or [])))
    redis_client = get_sync_redis_client()
    if len(unique_ids) > _SOFT_DELETE_BATCH_SIZE:
        for end_user_id in unique_ids:
            _release_soft_delete_inflight(
                redis_client,
                end_user_id,
                effective_batch_id,
            )
        return {
            "status": "FAILED",
            "reason": "batch_too_large",
            "batch_id": effective_batch_id,
            "received": len(unique_ids),
            "limit": _SOFT_DELETE_BATCH_SIZE,
        }

    success_count = 0
    fail_count = 0
    skipped_count = 0
    failed_ids: List[str] = []

    def _delete_one(end_user_id: str) -> str:
        if redis_client is None:
            raise RuntimeError("Redis unavailable; memory write lock cannot be acquired")

        parsed_id = uuid.UUID(end_user_id)
        write_lock = RedisFairLock(
            key=f"memory_write:{end_user_id}",
            redis_client=redis_client,
            expire=1200,
            timeout=60,
            auto_renewal=True,
        )
        with write_lock:
            with get_db_context() as db:
                repository = EndUserRepository(db)
                if not repository.is_expired_temporary_end_user(parsed_id):
                    return "skipped"
                if not repository.soft_delete_by_end_user_id(parsed_id):
                    raise RuntimeError("EndUser was no longer active during soft delete")
        return "success"

    try:
        for end_user_id in unique_ids:
            try:
                outcome = _delete_one(end_user_id)
                if outcome == "success":
                    success_count += 1
                else:
                    skipped_count += 1
            except SoftTimeLimitExceeded:
                logger.error(
                    f"[ExpiredEndUserDelete] 达到软超时: batch_id={effective_batch_id}, "
                    f"end_user_id={end_user_id}"
                )
                raise
            except Exception as e:
                fail_count += 1
                failed_ids.append(end_user_id)
                logger.error(
                    f"[ExpiredEndUserDelete] 清理失败: end_user_id={end_user_id}, error={e}",
                    exc_info=True,
                )
            finally:
                _release_soft_delete_inflight(
                    redis_client,
                    end_user_id,
                    effective_batch_id,
                )
    finally:
        for end_user_id in unique_ids:
            _release_soft_delete_inflight(
                redis_client,
                end_user_id,
                effective_batch_id,
            )

    elapsed = time.time() - started_at
    logger.info(
        f"[ExpiredEndUserDelete] 批次完成: batch_id={effective_batch_id}, "
        f"success={success_count}, failed={fail_count}, skipped={skipped_count}, "
        f"elapsed={elapsed:.1f}s"
    )
    return {
        "status": "SUCCESS" if fail_count == 0 else "PARTIAL_FAILURE",
        "batch_id": effective_batch_id,
        "success": success_count,
        "failed": fail_count,
        "skipped": skipped_count,
        "failed_ids": failed_ids,
        "elapsed_time": elapsed,
    }


@celery_app.task(
    name="app.tasks.scan_forget_candidates",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_forget_candidates(self) -> Dict[str, Any]:
    """扫描 Redis 中超过配额的用户，派发 do_forget_for_user。

    通过 smove 原子地将 end_user_id 从 candidates → inflight，
    避免重复派发同一个用户。
    """

    async def _run() -> Dict[str, Any]:
        from app.aioRedis import get_thread_safe_redis

        start_time = time.time()
        redis_client = get_thread_safe_redis()
        if redis_client is None:
            return {"status": "FAILED", "message": "Redis 不可用"}

        candidates = await redis_client.smembers(_FORGET_CANDIDATES_KEY)
        if not candidates:
            return {"status": "SUCCESS", "dispatched": 0}

        dispatched = 0
        skipped_inflight = 0
        for uid in candidates:
            if await redis_client.sismember(_FORGET_INFLIGHT_KEY, uid):
                await redis_client.srem(_FORGET_CANDIDATES_KEY, uid)
                skipped_inflight += 1
                continue

            moved = await redis_client.smove(_FORGET_CANDIDATES_KEY, _FORGET_INFLIGHT_KEY, uid)
            if not moved:
                skipped_inflight += 1
                continue
            try:
                do_forget_for_user.apply_async(
                    kwargs={"end_user_id": uid},
                    queue="memory_heavy_tasks",
                )
                dispatched += 1
            except Exception as e:
                await redis_client.smove(_FORGET_INFLIGHT_KEY, _FORGET_CANDIDATES_KEY, uid)
                logger.error(f"[ForgetScan] 派发失败 user={uid}: {e}")

        logger.info(
            f"[ForgetScan] 完成: dispatched={dispatched}/{len(candidates)}, "
            f"skip_inflight={skipped_inflight}, "
            f"耗时={time.time() - start_time:.1f}s"
        )
        return {
            "status": "SUCCESS",
            "dispatched": dispatched,
            "candidates": len(candidates),
            "skip_inflight": skipped_inflight,
        }

    return asyncio.run(_run())


@celery_app.task(
    name="app.tasks.do_forget_for_user",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=1200,
    soft_time_limit=1080,
)
def do_forget_for_user(self, end_user_id: str) -> Dict[str, Any]:
    """对单个用户执行遗忘。由 scan_forget_candidates 派发。

    ForgettingPipeline.run() 内部已调用 sync，sync 会根据最新计数
    自行决定 sadd / srem。这里只清理 inflight，保证不泄漏。
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.repositories.end_user_repository import get_by_id as _get_user
        from app.services.memory_config_service import MemoryConfigService
        from app.core.memory.memory_service import MemoryService
        redis_client = get_thread_safe_redis()
        with get_db_context() as db:
            end_user = _get_user(db, uuid.UUID(end_user_id))
            if end_user is None:
                logger.warning(f"[ForgetDo] 用户不存在: {end_user_id}")
                if redis_client:
                    await redis_client.srem(_FORGET_INFLIGHT_KEY, end_user_id)
                    await redis_client.srem(_FORGET_CANDIDATES_KEY, end_user_id)
                return {"status": "skipped", "reason": "not_found"}

            config_id = MemoryConfigService(db).get_workspace_active_config_id(end_user.workspace_id)
            workspace_id = str(end_user.workspace_id)

        service = MemoryService(
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )

        # 抢该用户的写锁：与反思 / 去重任务互斥，保证同一用户的图谱不被并发修改。
        # Celery 任务线程独占 event loop，阻塞不影响其他任务，直接用同步上下文管理器。
        sync_redis = get_sync_redis_client()
        if sync_redis is not None:
            write_lock = RedisFairLock(
                key=f"memory_write:{end_user_id}",
                redis_client=sync_redis,
                expire=1200, timeout=60, auto_renewal=True,
            )
            try:
                with write_lock:
                    result = await service.forget()
            except RuntimeError:
                logger.warning(
                    f"[ForgetDo] 获取写锁超时，跳过 user={end_user_id} "
                    "forget_lock_timeout_count=1"
                )
                if redis_client:
                    # 移回候选集，等下一轮 scan 重新派发（与 scan_forget_candidates 派发失败的处理一致）
                    await redis_client.smove(_FORGET_INFLIGHT_KEY, _FORGET_CANDIDATES_KEY, end_user_id)
                return {"status": "lock_timeout"}
        else:
            result = await service.forget()

        if redis_client:
            await redis_client.srem(_FORGET_INFLIGHT_KEY, end_user_id)

        logger.info(
            f"[ForgetDo] 完成: end_user_id={end_user_id}, "
            f"elapsed={time.time() - start_time:.1f}s"
        )
        return {"status": "success", "result": result}

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except Exception as e:
        logger.error(f"[ForgetDo] 失败 user={end_user_id}: {e}", exc_info=True)
        result = {"status": "failed", "error": str(e)}
    finally:
        _shutdown_loop_gracefully(loop)

    result["end_user_id"] = end_user_id
    result["elapsed_time"] = time.time() - start_time
    return result


# =============================================================================
# 隐性记忆和情绪数据更新：扫描-派发模式
# =============================================================================

_IMPLICIT_EMOTIONS_INFLIGHT_KEY_FMT = "implicit_emotions:inflight:{end_user_id}"
_IMPLICIT_EMOTIONS_INFLIGHT_TTL_SEC = 600
_INIT_EMOTIONS_INFLIGHT_KEY_FMT = "init_emotions:inflight:{end_user_id}"
_INIT_EMOTIONS_INFLIGHT_TTL_SEC = 600


# 需要在work-periodic执行扫描任务
@celery_app.task(
    name="app.tasks.scan_implicit_emotions_storage",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=300,
    soft_time_limit=270,
)
def scan_implicit_emotions_storage(self) -> Dict[str, Any]:
    """扫描器：分页读取需刷新的用户 ID，逐用户派发 do_implicit_emotions_for_user。

    替代旧 update_implicit_emotions_storage 单任务串行模式。
    每个用户一个独立 Celery 任务，独立重试、独立超时、故障隔离。
    """
    from app.repositories.implicit_emotions_storage_repository import (
        ImplicitEmotionsStorageRepository,
        TimeFilterUnavailableError,
    )

    start_time = time.time()
    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("scan_implicit_emotions_storage 终止：Redis 不可用，拒绝无锁派发")
        raise RuntimeError("Redis unavailable: implicit emotions scan requires inflight locks")

    dispatched = 0
    skip_inflight = 0

    # --- 短 session：收集需刷新的用户 ID 列表后立即关闭 ---
    user_ids: list[str] = []
    new_user_ids: list[str] = []
    with get_db_context() as db:
        repo = ImplicitEmotionsStorageRepository(db)
        try:
            user_ids = list(repo.get_users_needing_refresh(redis_client, batch_size=200))
        except TimeFilterUnavailableError as e:
            logger.warning(f"时间轴筛选不可用，回退到全量: {e}")
            user_ids = list(repo.get_all_user_ids(batch_size=200))
        except Exception as e:
            logger.warning(f"获取需刷新用户列表异常，回退到全量: {e}")
            user_ids = list(repo.get_all_user_ids(batch_size=200))
        new_user_ids = list(repo.get_new_user_ids_today(batch_size=200))
    # --- session 已关闭 ---

    all_ids = list(set(user_ids + new_user_ids))  # 去重：用户可能同时出现在存量和新用户列表中
    logger.info(
        f"scan_implicit_emotions_storage: 存量需刷新 {len(user_ids)}, "
        f"当天新增 {len(new_user_ids)}, 总候选 {len(all_ids)}"
    )

    for end_user_id in all_ids:
        # 互斥检查：若 init 任务正在处理该用户，跳过
        init_inflight_key = _INIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
        if redis_client.exists(init_inflight_key):
            skip_inflight += 1
            continue

        # inflight 锁：防止重复派发
        inflight_key = _IMPLICIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
        ok = redis_client.set(inflight_key, "1", nx=True, ex=_IMPLICIT_EMOTIONS_INFLIGHT_TTL_SEC)
        if not ok:
            skip_inflight += 1
            continue

        do_implicit_emotions_for_user.apply_async(
            kwargs={"end_user_id": end_user_id},
            queue="memory_heavy_tasks",
        )
        dispatched += 1

    elapsed = time.time() - start_time
    logger.info(
        f"scan_implicit_emotions_storage 完成: 派发 {dispatched}, "
        f"跳过(在途) {skip_inflight}, 耗时 {elapsed:.1f}s"
    )
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "skip_inflight": skip_inflight,
        "total_candidates": len(all_ids),
        "elapsed_time": elapsed,
        "task_id": self.request.id,
    }


@celery_app.task(
    name="app.tasks.do_implicit_emotions_for_user",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def do_implicit_emotions_for_user(self, end_user_id: str) -> Dict[str, Any]:
    """对【单个用户】生成隐性记忆画像 + 情绪建议。

    由 scan_implicit_emotions_storage 派发，每个用户一个独立 Celery 任务。
    三段式短 session：Session A（工厂方法内）→ LLM（无 PG）→ Session B（写回）。
    """
    start_time = time.time()
    inflight_key = _IMPLICIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)

    async def _run() -> Dict[str, Any]:
        from app.services.emotion_analytics_service import EmotionAnalyticsService
        from app.services.implicit_memory_service import ImplicitMemoryService

        implicit_success = False
        emotion_success = False
        errors = []

        # --- 隐性记忆画像 ---
        try:
            # Session A 内置于工厂方法（短 session 查 config + 构造 LLM 客户端 → 关闭）
            implicit_service = ImplicitMemoryService.create_without_session(end_user_id)

            # LLM + Neo4j 生成（无 PG session）
            try:
                profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
            finally:
                # 释放独立 Neo4j driver 连接池，防止泄漏
                await implicit_service.neo4j_connector.close()

            # Session B：写回
            with get_db_context() as db:
                await implicit_service.save_profile_cache(
                    end_user_id=end_user_id, profile_data=profile_data, db=db
                )
            implicit_success = True
            logger.info(f"成功更新用户 {end_user_id} 的隐性记忆画像")
        except Exception as e:
            errors.append(f"隐性记忆更新失败: {str(e)}")
            logger.error(f"用户 {end_user_id} 隐性记忆更新失败: {e}")

        # --- 情绪建议 ---
        # worker 进程内事件循环可能被其它任务的 asyncio.run() 更换，
        # 共享 driver 绑定旧 loop 会报 "Future attached to a different loop"，
        # 故与上方隐性记忆部分一致：任务级独立 driver，finally 中关闭。
        try:
            emotion_service = EmotionAnalyticsService(shared_driver=False)
            try:
                # db=None：内部自行开短 session 查 config → 关闭 → Neo4j + LLM
                suggestions_data = await emotion_service.generate_emotion_suggestions(
                    end_user_id=end_user_id, language="zh"
                )

                # Session C：写回
                with get_db_context() as db:
                    await emotion_service.save_suggestions_cache(
                        end_user_id=end_user_id, suggestions_data=suggestions_data, db=db
                    )
                emotion_success = True
                logger.info(f"成功更新用户 {end_user_id} 的情绪建议")
            finally:
                # 尽力清理：关闭失败仅记录，避免覆盖上方业务原始异常
                try:
                    await emotion_service.emotion_repo.connector.close()
                except Exception as close_err:
                    logger.warning(f"用户 {end_user_id} 关闭情绪分析独立 driver 失败: {close_err}")
        except Exception as e:
            errors.append(f"情绪建议更新失败: {str(e)}")
            logger.error(f"用户 {end_user_id} 情绪建议更新失败: {e}")

        return {
            "implicit_success": implicit_success,
            "emotion_success": emotion_success,
            "errors": errors,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        # 双失败 = 完全失败
        if not result["implicit_success"] and not result["emotion_success"]:
            raise RuntimeError(
                f"implicit and emotion both failed for user {end_user_id}: {result['errors']}"
            )
        result["status"] = (
            "success" if (result["implicit_success"] and result["emotion_success"]) else "partial"
        )
        logger.info(
            f"do_implicit_emotions_for_user 完成 user={end_user_id} "
            f"status={result['status']} 耗时={time.time() - start_time:.1f}s"
        )
    finally:
        # 清理 pending tasks + asyncgens，但不关闭 loop：
        # shared_driver=True 的 Neo4j 连接池绑定在此 loop 上，关闭会导致后续任务
        # 报 "Future attached to a different loop"。loop 在 worker 进程内复用。
        _shutdown_loop_gracefully(loop)
        # 删除在途标记
        try:
            _rc = get_sync_redis_client()
            if _rc is not None:
                _rc.delete(inflight_key)
        except Exception:
            pass

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    result["end_user_id"] = end_user_id
    return result


# =============================================================================
# 情绪统计明细：扫描-派发模式（Neo4j Dialogue.emotion → PG dialogue_emotion_raw）
# =============================================================================

_EMOTION_STATS_INFLIGHT_KEY_FMT = "emotion_stats:inflight:{end_user_id}"
_EMOTION_STATS_INFLIGHT_TTL_SEC = 1800


# 需要在work-periodic执行扫描任务
@celery_app.task(
    name="app.tasks.scan_emotion_stats",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=300,
    soft_time_limit=270,
)
def scan_emotion_stats(self) -> Dict[str, Any]:
    """扫描器：write_time 过滤活跃用户，逐用户派发 sync_emotion_stats_for_user。

    每天北京时间凌晨 1:00（UTC 17:00）由 Beat 触发，增量同步昨日（北京时间）数据。
    活跃过滤只是粗过滤：精确判断（窗口选择 24h/48h）由用户级任务查 PG
    dialogue_emotion_raw「北京前天」是否有数据完成，Neo4j 仅负责拉取窗口内原始对话。
    """
    from app.repositories.dialogue_emotion_raw_repository import (
        DialogueEmotionRawRepository,
    )

    start_time = time.time()
    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("scan_emotion_stats 终止：Redis 不可用，拒绝无锁派发")
        raise RuntimeError("Redis unavailable: emotion stats scan requires inflight locks")

    dispatched = 0
    skip_inflight = 0
    failed = 0

    # --- 短 session：49 小时内活跃（write_time）的用户 ID 列表后立即关闭 ---
    # 49h（而非 25h）：write_time 延迟写入的用户（前天活跃、昨天才写上）也能捞回，
    # 配合用户任务按 PG 判断的 24h/48h 窗口补齐漏掉的日子
    with get_db_context() as db:
        repo = DialogueEmotionRawRepository(db)
        active_ids = repo.get_active_end_user_ids(active_within_hours=49)
    # --- session 已关闭 ---

    logger.info(
        f"scan_emotion_stats: 49h 内活跃用户 {len(active_ids)} 个，开始派发增量同步"
    )

    import uuid
    for end_user_id in active_ids:
        # inflight 锁：防止重复派发，生成随机 token 避免误删新锁
        inflight_key = _EMOTION_STATS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
        inflight_token = uuid.uuid4().hex
        try:
            ok = redis_client.set(
                inflight_key, inflight_token, nx=True, ex=_EMOTION_STATS_INFLIGHT_TTL_SEC
            )
        except Exception as e:
            logger.warning(f"scan_emotion_stats 设置在途锁失败: {end_user_id}, {e}")
            failed += 1
            continue
        if not ok:
            skip_inflight += 1
            continue

        try:
            sync_emotion_stats_for_user.apply_async(
                kwargs={"end_user_id": end_user_id, "inflight_token": inflight_token},
                queue="memory_heavy_tasks",
            )
            dispatched += 1
        except Exception as e:
            # 派发失败回滚锁，允许下次扫描重试
            logger.error(f"scan_emotion_stats 派发失败: {end_user_id}, {e}")
            try:
                # 只有 value matches 才删除，避免误删其他 worker 的锁
                redis_client.eval(UNLOCK_SCRIPT, 1, inflight_key, inflight_token)
            except Exception:
                pass
            failed += 1

    elapsed = time.time() - start_time
    logger.info(
        f"scan_emotion_stats 完成: 派发 {dispatched}, 跳过(在途) {skip_inflight}, "
        f"失败 {failed}, 活跃候选 {len(active_ids)}, 耗时 {elapsed:.1f}s"
    )
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "skip_inflight": skip_inflight,
        "failed": failed,
        "total_candidates": len(active_ids),
        "elapsed_time": elapsed,
        "task_id": self.request.id,
    }


@celery_app.task(
    name="app.tasks.sync_emotion_stats_for_user",
    bind=True,
    ignore_result=False,
    max_retries=3,
    acks_late=False,
    time_limit=300,
    soft_time_limit=270,
)
def sync_emotion_stats_for_user(
    self, end_user_id: str, inflight_token: str | None = None
) -> Dict[str, Any]:
    """对【单个用户】同步情绪明细：按 PG 入库情况选窗口（北京时间基准）。

    由 scan_emotion_stats 派发，每个用户一个独立 Celery 任务。
    窗口选择（查 PG dialogue_emotion_raw，与扫描器的 write_time 分工互补）：
    - PG「北京前天」已有明细 → 只扫北京昨天 24h（平时路径，减少 Neo4j 扫描量）；
    - PG「北京前天」无明细   → 补扫前天+昨天 48h（漏扫兜底：write_time 延迟/
      单次漏派导致的 1~2 天缺口在下一轮补齐，Upsert 幂等重扫安全）。
    某天无带情绪对话时不写行（不产生假记录）。失败自动重试（max_retries=3）。

    Args:
        end_user_id: 目标用户 ID
        inflight_token: 在途锁 token，用于校验锁归属（避免误删新锁）
    """
    from app.repositories.neo4j.neo4j_connector import Neo4jConnector
    from app.services.emotion_stats_service import EmotionStatsService

    start_time = time.time()
    inflight_key = _EMOTION_STATS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)

    # 在途锁校验：锁已过期或被新一轮 scan 重设时跳过（token 不匹配）
    _rc = get_sync_redis_client()
    if _rc is not None and inflight_token:
        if _rc.get(inflight_key) != inflight_token:
            logger.warning(
                f"sync_emotion_stats_for_user 跳过 user={end_user_id} "
                f"在途锁已失效（过期或被新 scan 重设）"
            )
            result = {"status": "SKIPPED_STALE_INFLIGHT"}
            result["elapsed_time"] = time.time() - start_time
            result["task_id"] = self.request.id
            result["end_user_id"] = end_user_id
            return result

    def _compute_window():
        """按 PG 入库情况选窗口（北京时间基准，短 session 查询后立即关闭）

        PG「北京前天」有明细 → 24h（只扫昨天）；
        无明细（可能漏扫）   → 48h（补扫前天+昨天）。
        判断与 write_time 无关：write_time 管派发名单（源侧活跃），
        PG 明细管窗口选择（入库进度），二者不可互换。
        """
        from app.repositories.dialogue_emotion_raw_repository import (
            DialogueEmotionRawRepository,
        )

        _, end_dt = EmotionStatsService.get_yesterday_beijing_window()
        with get_db_context() as db:
            repo = DialogueEmotionRawRepository(db)
            has_prev = repo.has_dialogue_in_utc_range(
                end_user_id, end_dt - timedelta(days=2), end_dt - timedelta(days=1)
            )
        # 前天已有数据说明上一轮已扫过前天 → 只扫昨天；否则补扫两天
        start_dt = end_dt - timedelta(days=1) if has_prev else end_dt - timedelta(days=2)
        return start_dt, end_dt

    async def _run() -> Dict[str, Any]:
        start_dt, end_dt = _compute_window()
        # 每次任务独立创建非共享 driver（绑定当前 loop），用完即关——不跨任务/跨 loop 复用
        connector = Neo4jConnector()
        return await EmotionStatsService.sync_range_for_user(
            end_user_id=end_user_id,
            start_dt=start_dt,
            end_dt=end_dt,
            connector=connector,
            close_connector=True,
        )

    loop = set_asyncio_event_loop()
    retrying = False
    try:
        result = loop.run_until_complete(_run())
        result["status"] = "SUCCESS"
        logger.info(
            f"sync_emotion_stats_for_user 完成 user={end_user_id} "
            f"rows={result.get('total_dialogues', 0)} "
            f"耗时={time.time() - start_time:.1f}s"
        )
    except Exception as exc:
        logger.error(f"sync_emotion_stats_for_user 失败 user={end_user_id}: {exc}")
        # 失败自动重试（60s 间隔），Upsert 幂等保证重试安全
        retrying = True
        raise self.retry(exc=exc, countdown=60)
    finally:
        # 清理 pending tasks + asyncgens，保留 loop 供当前线程的下个任务复用
        _shutdown_loop_gracefully(loop)
        # 解锁时机：
        # 1. 成功（retrying=False）：主动删除在途锁，下轮扫描可重新派发；
        # 2. 重试排队期间（retrying=True）：保留锁，TTL(1800s) > 重试间隔(60s)，
        #    避免扫描器本轮重复派发同一用户导致并发执行；
        # 3. 最终失败（max_retries 用尽，retrying=True）：仍保留锁，
        #    靠 TTL 1800s 过期兜底，30 分钟后消失，次日扫描可重新派发。
        if not retrying:
            try:
                _rc = get_sync_redis_client()
                if _rc is not None and inflight_token:
                    # 原子检查：只有 value matches 才删除，避免误删新锁
                    _rc.eval(UNLOCK_SCRIPT, 1, inflight_key, inflight_token)
            except Exception:
                pass

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    result["end_user_id"] = end_user_id
    return result


# =============================================================================
# 隐性记忆和情绪数据更新定时任务（已废弃，由 scan_implicit_emotions_storage + do_implicit_emotions_for_user 替代）
# =============================================================================

@celery_app.task(
    name="app.tasks.update_implicit_emotions_storage",
    bind=True,
    ignore_result=True,
    max_retries=0,
    acks_late=False,
    time_limit=7200,  # 2小时硬超时
    soft_time_limit=6900,  # 1小时55分钟软超时
)
def update_implicit_emotions_storage(self) -> Dict[str, Any]:
    """定时任务：更新所有用户的隐性记忆画像和情绪建议数据·

    遍历数据库中所有已存在数据的用户，为每个用户重新生成隐性记忆画像和情绪建议。
    实现错误隔离，单个用户失败不影响其他用户的处理。

    Returns:
        包含任务执行结果的字典，包括：
        - status: 任务状态 (SUCCESS/FAILURE)
        - message: 执行消息
        - total_users: 总用户数
        - successful_implicit: 成功更新隐性记忆的用户数
        - successful_emotion: 成功更新情绪建议的用户数
        - failed: 失败的用户数
        - user_results: 每个用户的详细结果
        - elapsed_time: 执行耗时（秒）
        - task_id: 任务ID
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from sqlalchemy import select

        from app.models.implicit_emotions_storage_model import ImplicitEmotionsStorage
        from app.repositories.implicit_emotions_storage_repository import (
            ImplicitEmotionsStorageRepository,
            TimeFilterUnavailableError,
        )
        from app.services.emotion_analytics_service import EmotionAnalyticsService
        from app.services.implicit_memory_service import ImplicitMemoryService

        logger.info("开始执行隐性记忆和情绪数据更新定时任务")

        total_users = 0
        successful_implicit = 0
        successful_emotion = 0
        failed = 0
        user_results = []

        with get_db_context() as db:
            repo = ImplicitEmotionsStorageRepository(db)

            # 先统计总数用于日志
            from sqlalchemy import func
            total_users = db.execute(
                select(func.count()).select_from(ImplicitEmotionsStorage)
            ).scalar() or 0
            logger.info(f"表中存量用户总数: {total_users}，开始时间轴筛选")

            # 构建 Redis 同步客户端，用于时间轴筛选
            _redis_client = get_sync_redis_client()

            # 只处理 last_done > updated_at 的用户（有新记忆写入的用户）
            # Redis 不可用时回退到全量处理
            try:
                refresh_iter = repo.get_users_needing_refresh(_redis_client, batch_size=100)
            except TimeFilterUnavailableError as e:
                logger.warning(f"时间轴筛选不可用，回退到全量刷新: {e}")
                refresh_iter = repo.get_all_user_ids(batch_size=100)

            for end_user_id in refresh_iter:
                logger.info(f"开始处理用户: {end_user_id}")
                user_start_time = time.time()

                implicit_success = False
                emotion_success = False
                errors = []

                try:
                    # 更新隐性记忆画像
                    try:
                        implicit_service = ImplicitMemoryService(db=db, end_user_id=end_user_id)
                        profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
                        await implicit_service.save_profile_cache(
                            end_user_id=end_user_id,
                            profile_data=profile_data,
                            db=db
                        )
                        implicit_success = True
                        logger.info(f"成功更新用户 {end_user_id} 的隐性记忆画像")
                    except Exception as e:
                        error_msg = f"隐性记忆更新失败: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"用户 {end_user_id} {error_msg}")

                    # 更新情绪建议（独立 driver：loop 可能被其它任务的 asyncio.run() 更换）
                    try:
                        emotion_service = EmotionAnalyticsService(shared_driver=False)
                        try:
                            suggestions_data = await emotion_service.generate_emotion_suggestions(
                                end_user_id=end_user_id,
                                db=db,
                                language="zh"
                            )
                            await emotion_service.save_suggestions_cache(
                                end_user_id=end_user_id,
                                suggestions_data=suggestions_data,
                                db=db
                            )
                            emotion_success = True
                            logger.info(f"成功更新用户 {end_user_id} 的情绪建议")
                        finally:
                            # 尽力清理：关闭失败仅记录，避免覆盖上方业务原始异常
                            try:
                                await emotion_service.emotion_repo.connector.close()
                            except Exception as close_err:
                                logger.warning(f"用户 {end_user_id} 关闭情绪分析独立 driver 失败: {close_err}")
                    except Exception as e:
                        error_msg = f"情绪建议更新失败: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"用户 {end_user_id} {error_msg}")

                    # 统计结果
                    if implicit_success:
                        successful_implicit += 1
                    if emotion_success:
                        successful_emotion += 1
                    if not implicit_success and not emotion_success:
                        failed += 1

                    user_elapsed = time.time() - user_start_time

                    # 记录用户处理结果
                    user_result = {
                        "end_user_id": end_user_id,
                        "implicit_success": implicit_success,
                        "emotion_success": emotion_success,
                        "errors": errors,
                        "elapsed_time": user_elapsed
                    }
                    user_results.append(user_result)

                    logger.info(
                        f"用户 {end_user_id} 处理完成: "
                        f"隐性记忆={'成功' if implicit_success else '失败'}, "
                        f"情绪建议={'成功' if emotion_success else '失败'}, "
                        f"耗时={user_elapsed:.2f}秒"
                    )

                except Exception as e:
                    # 单个用户失败不影响其他用户（错误隔离）
                    failed += 1
                    user_elapsed = time.time() - user_start_time
                    error_info = {
                        "end_user_id": end_user_id,
                        "implicit_success": False,
                        "emotion_success": False,
                        "errors": [str(e)],
                        "elapsed_time": user_elapsed
                    }
                    user_results.append(error_info)
                    logger.error(f"处理用户 {end_user_id} 时出错: {str(e)}")

            # ---- 当天新增用户兜底初始化 ----
            new_users_initialized = 0
            new_users_failed = 0
            logger.info("开始处理当天新增用户的兜底初始化")

            for end_user_id in repo.get_new_user_ids_today(batch_size=100):
                logger.info(f"开始初始化新用户: {end_user_id}")
                user_start_time = time.time()
                implicit_success = False
                emotion_success = False
                errors = []

                try:
                    try:
                        implicit_service = ImplicitMemoryService(db=db, end_user_id=end_user_id)
                        profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
                        await implicit_service.save_profile_cache(
                            end_user_id=end_user_id, profile_data=profile_data, db=db
                        )
                        implicit_success = True
                        logger.info(f"成功初始化新用户 {end_user_id} 的隐性记忆画像")
                    except Exception as e:
                        errors.append(f"隐性记忆初始化失败: {str(e)}")
                        logger.error(f"新用户 {end_user_id} 隐性记忆初始化失败: {e}")

                    # 独立 driver：loop 可能被其它任务的 asyncio.run() 更换
                    try:
                        emotion_service = EmotionAnalyticsService(shared_driver=False)
                        try:
                            suggestions_data = await emotion_service.generate_emotion_suggestions(
                                end_user_id=end_user_id, db=db, language="zh"
                            )
                            await emotion_service.save_suggestions_cache(
                                end_user_id=end_user_id, suggestions_data=suggestions_data, db=db
                            )
                            emotion_success = True
                            logger.info(f"成功初始化新用户 {end_user_id} 的情绪建议")
                        finally:
                            # 尽力清理：关闭失败仅记录，避免覆盖上方业务原始异常
                            try:
                                await emotion_service.emotion_repo.connector.close()
                            except Exception as close_err:
                                logger.warning(f"用户 {end_user_id} 关闭情绪分析独立 driver 失败: {close_err}")
                    except Exception as e:
                        errors.append(f"情绪建议初始化失败: {str(e)}")
                        logger.error(f"新用户 {end_user_id} 情绪建议初始化失败: {e}")

                    if implicit_success or emotion_success:
                        new_users_initialized += 1
                    else:
                        new_users_failed += 1

                    user_elapsed = time.time() - user_start_time
                    user_results.append({
                        "end_user_id": end_user_id,
                        "type": "new_user_init",
                        "implicit_success": implicit_success,
                        "emotion_success": emotion_success,
                        "errors": errors,
                        "elapsed_time": user_elapsed
                    })

                except Exception as e:
                    new_users_failed += 1
                    user_elapsed = time.time() - user_start_time
                    user_results.append({
                        "end_user_id": end_user_id,
                        "type": "new_user_init",
                        "implicit_success": False,
                        "emotion_success": False,
                        "errors": [str(e)],
                        "elapsed_time": user_elapsed
                    })
                    logger.error(f"初始化新用户 {end_user_id} 时出错: {str(e)}")

            logger.info(f"当天新增用户兜底初始化完成: 成功={new_users_initialized}, 失败={new_users_failed}")
            # ---- 新增用户兜底初始化结束 ----

            logger.info(
                f"隐性记忆和情绪数据更新定时任务完成: "
                f"存量用户总数={total_users}, "
                f"隐性记忆成功={successful_implicit}, "
                f"情绪建议成功={successful_emotion}, "
                f"存量失败={failed}, "
                f"新增用户初始化成功={new_users_initialized}, "
                f"新增用户初始化失败={new_users_failed}"
            )

            return {
                "status": "SUCCESS",
                "message": (
                    f"存量用户 {total_users} 个，隐性记忆 {successful_implicit} 个成功，情绪建议 {successful_emotion} 个成功；"
                    f"当天新增用户初始化 {new_users_initialized} 个成功，{new_users_failed} 个失败"
                ),
                "total_users": total_users,
                "successful_implicit": successful_implicit,
                "successful_emotion": successful_emotion,
                "failed": failed,
                "new_users_initialized": new_users_initialized,
                "new_users_failed": new_users_failed,
                "user_results": user_results[:50]
            }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)


# =============================================================================

@celery_app.task(
    name="app.tasks.init_implicit_emotions_for_users",
    bind=True,
    ignore_result=True,
    max_retries=0,
    acks_late=False,
    time_limit=3600,
    soft_time_limit=3300,
    # 触发型任务标识，区别于 periodic_tasks 队列中的定时任务
    triggered=True,
)
def init_implicit_emotions_for_users(self, end_user_ids: List[str]) -> Dict[str, Any]:
    """事件触发任务：对指定用户列表做存在性检查，无记录则执行首次初始化。

    由 /dashboard/end_users 接口触发，已有数据的用户直接跳过。
    存量用户的数据刷新由定时任务 scan_implicit_emotions_storage 负责。

    改造说明：逐用户三段式短 session + per-user inflight 锁，
    避免单个 session 跨多用户 LLM 调用期间空占 PG 连接。

    Args:
        end_user_ids: 需要检查的用户ID列表

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.repositories.implicit_emotions_storage_repository import (
            ImplicitEmotionsStorageRepository,
        )
        from app.services.emotion_analytics_service import EmotionAnalyticsService
        from app.services.implicit_memory_service import ImplicitMemoryService

        logger.info(f"开始按需初始化隐性记忆/情绪数据，候选用户数: {len(end_user_ids)}")

        redis_client = get_sync_redis_client()
        if redis_client is None:
            logger.error("init_implicit_emotions_for_users 终止：Redis 不可用，拒绝无锁执行")
            raise RuntimeError("Redis unavailable: init implicit emotions requires inflight locks")

        initialized = 0
        failed = 0
        skip_inflight = 0
        skip_existing = 0

        for end_user_id in end_user_ids:
            # 互斥检查：若 scan 派发的 do_implicit_emotions_for_user 正在处理该用户，跳过
            scan_inflight_key = _IMPLICIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
            if redis_client.exists(scan_inflight_key):
                skip_inflight += 1
                continue

            # Per-user inflight 锁：防止与 scan 任务并发处理同一用户
            inflight_key = _INIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
            ok = redis_client.set(inflight_key, "1", nx=True, ex=_INIT_EMOTIONS_INFLIGHT_TTL_SEC)
            if not ok:
                skip_inflight += 1
                continue

            try:
                # --- Session A：查存在性 → 关闭 ---
                existing = None
                with get_db_context() as db:
                    existing = ImplicitEmotionsStorageRepository(db).get_by_end_user_id(end_user_id)

                if existing is not None:
                    skip_existing += 1
                    continue

                logger.info(f"用户 {end_user_id} 无记录，开始初始化")
                implicit_ok = False
                emotion_ok = False

                # --- 隐性记忆画像：LLM 生成（无 PG session）---
                try:
                    implicit_service = ImplicitMemoryService.create_without_session(end_user_id)
                    try:
                        profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
                    finally:
                        # 释放独立 Neo4j driver 连接池，防止泄漏
                        await implicit_service.neo4j_connector.close()
                    # Session B：写回
                    with get_db_context() as db:
                        await implicit_service.save_profile_cache(
                            end_user_id=end_user_id, profile_data=profile_data, db=db
                        )
                    implicit_ok = True
                except Exception as e:
                    logger.error(f"用户 {end_user_id} 隐性记忆初始化失败: {e}")

                # --- 情绪建议：LLM 生成（内部自管理 session）---
                # 独立 driver：loop 可能被其它任务的 asyncio.run() 更换
                try:
                    emotion_service = EmotionAnalyticsService(shared_driver=False)
                    try:
                        suggestions_data = await emotion_service.generate_emotion_suggestions(
                            end_user_id=end_user_id, language="zh"
                        )
                        # Session C：写回
                        with get_db_context() as db:
                            await emotion_service.save_suggestions_cache(
                                end_user_id=end_user_id, suggestions_data=suggestions_data, db=db
                            )
                        emotion_ok = True
                    finally:
                        # 尽力清理：关闭失败仅记录，避免覆盖上方业务原始异常
                        try:
                            await emotion_service.emotion_repo.connector.close()
                        except Exception as close_err:
                            logger.warning(f"用户 {end_user_id} 关闭情绪分析独立 driver 失败: {close_err}")
                except Exception as e:
                    logger.error(f"用户 {end_user_id} 情绪建议初始化失败: {e}")

                if implicit_ok or emotion_ok:
                    initialized += 1
                else:
                    failed += 1

            except Exception as e:
                failed += 1
                logger.error(f"用户 {end_user_id} 初始化异常: {e}")
            finally:
                # 清理 inflight 锁
                try:
                    redis_client.delete(inflight_key)
                except Exception:
                    pass

        logger.info(
            f"按需初始化完成: 初始化={initialized}, "
            f"跳过(在途)={skip_inflight}, 跳过(已有)={skip_existing}, 失败={failed}"
        )
        return {
            "status": "SUCCESS",
            "initialized": initialized,
            "skipped": skip_inflight + skip_existing,
            "skip_inflight": skip_inflight,
            "skip_existing": skip_existing,
            "failed": failed,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        # 全部失败（无初始化、无跳过）= 完全失败：raise → Celery FAILURE
        if result["failed"] > 0 and result["initialized"] == 0 and result["skipped"] == 0:
            raise RuntimeError(
                f"all {result['failed']} users failed to initialize implicit emotions"
            )
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)


# =============================================================================

@celery_app.task(
    name="app.tasks.init_interest_distribution_for_users",
    bind=True,
    ignore_result=True,
    max_retries=0,
    acks_late=False,
    time_limit=3600,
    soft_time_limit=3300,
)
def init_interest_distribution_for_users(self, end_user_ids: List[str]) -> Dict[str, Any]:
    """事件触发任务：检查指定用户列表的兴趣分布缓存，无缓存则生成并写入 Redis。

    由 /dashboard/end_users 接口触发，已有缓存的用户直接跳过。
    默认生成中文（zh）兴趣分布数据。

    Args:
        self: task object
        end_user_ids: 需要检查的用户ID列表

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.cache.memory.interest_memory import InterestMemoryCache, INTEREST_CACHE_EXPIRE
        from app.services.memory_agent_service import MemoryAgentService

        logger.info(f"开始按需初始化兴趣分布缓存，候选用户数: {len(end_user_ids)}")

        initialized = 0
        failed = 0
        skipped = 0
        not_cached = 0
        language = "zh"

        service = MemoryAgentService()

        # 预校验：逐个解析 UUID，无效格式直接记 failed 跳过
        valid_uuids: list[uuid.UUID] = []
        invalid_ids: list[str] = []
        for eid in end_user_ids:
            try:
                valid_uuids.append(uuid.UUID(eid))
            except (ValueError, AttributeError):
                invalid_ids.append(eid)
                failed += 1
                logger.warning(f"用户 {eid} UUID 格式无效，跳过兴趣分布初始化")

        # 查询 DB 中实际存在的 end_user_id
        with get_db_context() as db:
            from app.repositories.end_user_repository import EndUserRepository
            existing_ids = EndUserRepository(db).filter_existing_ids(valid_uuids)

        for end_user_id in end_user_ids:
            # 存在性校验：不存在的用户直接记失败
            if end_user_id not in existing_ids:
                failed += 1
                logger.warning(f"用户 {end_user_id} 不存在，跳过兴趣分布初始化")
                continue

            # 存在性检查：缓存有数据则跳过
            cached = await InterestMemoryCache.get_interest_distribution(
                end_user_id=end_user_id,
                language=language,
            )
            if cached is not None:
                skipped += 1
                continue

            logger.info(f"用户 {end_user_id} 无兴趣分布缓存，开始生成")
            try:
                result, cacheable = await service.generate_interest_distribution_by_user(
                    end_user_id=end_user_id,
                    limit=5,
                    language=language,
                )
                if cacheable:
                    await InterestMemoryCache.set_interest_distribution(
                        end_user_id=end_user_id,
                        language=language,
                        data=result,
                        expire=INTEREST_CACHE_EXPIRE,
                    )
                    initialized += 1
                    logger.info(f"用户 {end_user_id} 兴趣分布缓存生成成功")
                else:
                    not_cached += 1
                    logger.info(f"用户 {end_user_id} 兴趣分布结果不可缓存，本次不写缓存")
            except Exception as e:
                failed += 1
                logger.error(f"用户 {end_user_id} 兴趣分布缓存生成失败: {e}")

        logger.info(
            f"兴趣分布按需初始化完成: 初始化={initialized}, "
            f"未缓存={not_cached}, 跳过={skipped}, 失败={failed}"
        )
        return {
            "status": "SUCCESS",
            "initialized": initialized,
            "not_cached": not_cached,
            "skipped": skipped,
            "failed": failed,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        # 全部失败（无初始化、无未缓存成功结果、无跳过）才标记 Celery FAILURE。
        if (
                result["failed"] > 0
                and result["initialized"] == 0
                and result["not_cached"] == 0
                and result["skipped"] == 0
        ):
            raise RuntimeError(
                f"all {result['failed']} users failed to initialize interest distribution"
            )
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)


# =============================================================================
# 社区聚类补全任务（触发型）

def _resolve_community_clustering_owner_id(end_user_id: str) -> str:
    """Resolve a possibly merged user to the current active graph owner."""
    with get_db_context() as db:
        from app.repositories.end_user_repository import EndUserRepository

        resolved = EndUserRepository(db).resolve_merge_by_origin_id(
            uuid.UUID(end_user_id)
        )
        return str(resolved.id) if resolved else end_user_id


def _acquire_community_clustering_lock(
    original_end_user_id: str,
    *,
    redis_client,
    expire: int,
) -> tuple[str, RedisFairLock]:
    """Resolve, lock, and re-resolve until the lock protects current owner."""
    candidate_id = original_end_user_id
    while True:
        effective_id = _resolve_community_clustering_owner_id(candidate_id)
        write_lock = RedisFairLock(
            key=f"memory_write:{effective_id}",
            redis_client=redis_client,
            expire=expire,
            timeout=60,
            auto_renewal=True,
        )
        if not write_lock.acquire():
            raise RuntimeError(
                f"Get redis lock timeout: memory_write:{effective_id}"
            )
        try:
            confirmed_id = _resolve_community_clustering_owner_id(candidate_id)
        except Exception:
            write_lock.release()
            raise
        if confirmed_id == effective_id:
            return effective_id, write_lock
        write_lock.release()
        candidate_id = confirmed_id


# =============================================================================

@celery_app.task(
    name="app.tasks.run_incremental_clustering",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=1800,  # 30分钟硬超时
    soft_time_limit=1700,
)
def run_incremental_clustering(
        self,
        end_user_id: str,
        new_entity_ids: List[str],
        config_id: Optional[str] = None,
        language: str = "zh",
) -> Dict[str, Any]:
    """增量聚类任务：处理新增实体的社区分配和元数据生成。
    
    此任务在后台异步执行，不阻塞 write_message 主流程。
    
    Args:
        end_user_id: 用户 ID
        new_entity_ids: 新增实体 ID 列表
        config_id: 记忆配置 ID（可选）。任务内经 load_memory_config 重建完整
            MemoryConfig（内含 tenant_id + 各 model_id，同源），交由引擎使用。
        language: 语言类型 ("zh" | "en")
    
    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()
    original_end_user_id = end_user_id

    async def _run() -> Dict[str, Any]:
        from app.core.logging_config import get_logger
        from app.core.memory.storage.custom import CommunityMutationWriter
        from app.core.memory.storage.provider.neo4j.client import Neo4jClient
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.core.memory.storage_services.clustering_engine.label_propagation import LabelPropagationEngine

        logger = get_logger(__name__)
        logger.info(
            f"[IncrementalClustering] 开始增量聚类任务 - end_user_id={end_user_id}, "
            f"实体数={len(new_entity_ids)}, config_id={config_id}"
        )

        # 跨进程只传 config_id，任务内重建完整 MemoryConfig：
        # tenant_id 与各 model_id 同源加载，杜绝拍扁传参时漏传 tenant。
        with get_db_context() as db:
            from app.services.memory_config_service import MemoryConfigService
            memory_config = MemoryConfigService(db).load_memory_config(config_id=config_id)

        connector = Neo4jConnector()
        storage_client = None
        try:
            storage_client = await Neo4jClient.create()
            engine = LabelPropagationEngine(
                connector=connector,
                memory_config=memory_config,
                community_writer=CommunityMutationWriter(storage_client),
                language=language,
            )

            # 执行增量聚类
            await engine.run(end_user_id=end_user_id, new_entity_ids=new_entity_ids)

            logger.info(f"[IncrementalClustering] 增量聚类完成 - end_user_id={end_user_id}")

            return {
                "status": "SUCCESS",
                "end_user_id": end_user_id,
                "entity_count": len(new_entity_ids),
            }
        except Exception as e:
            logger.error(f"[IncrementalClustering] 增量聚类失败: {e}", exc_info=True)
            raise
        finally:
            try:
                if storage_client is not None:
                    await storage_client.close()
            finally:
                await connector.close()

    loop = set_asyncio_event_loop()
    write_lock = None
    try:
        end_user_id, write_lock = _acquire_community_clustering_lock(
            original_end_user_id,
            redis_client=get_thread_safe_sync_redis(),
            expire=1800,
        )
        result = loop.run_until_complete(_run())
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id

        logger.info(
            f"[IncrementalClustering] 任务完成 - task_id={self.request.id}, "
            f"elapsed_time={result['elapsed_time']:.2f}s"
        )

        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE
    finally:
        if write_lock is not None:
            write_lock.release()
        _shutdown_loop_gracefully(loop)


@celery_app.task(
    name="app.tasks.init_community_clustering_for_users",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=7200,  # 2小时硬超时
    soft_time_limit=6900,
)
def init_community_clustering_for_users(self, end_user_ids: List[str], workspace_id: Optional[str] = None) -> Dict[
    str, Any]:
    """触发型任务：检查指定用户列表，对有 ExtractedEntity 但无 Community 节点的用户执行全量聚类。

    由 /dashboard/end_users 接口触发，已有社区节点的用户直接跳过。
    任务完成且所有用户数据均完整时，写入 Redis 标记，避免下次重复投递。

    Args:
        end_user_ids: 需要检查的用户 ID 列表
        workspace_id: 工作空间 ID，用于完成标记

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.core.logging_config import get_logger
        from app.repositories.neo4j.community_repository import CommunityRepository
        from app.core.memory.storage.custom import CommunityMutationWriter
        from app.core.memory.storage.provider.neo4j.client import Neo4jClient
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.core.memory.storage_services.clustering_engine.label_propagation import LabelPropagationEngine

        logger = get_logger(__name__)
        logger.info(f"[CommunityCluster] 开始社区聚类补全任务，候选用户数: {len(end_user_ids)}")

        initialized = 0
        skipped = 0
        failed = 0

        connector = Neo4jConnector()
        storage_client = None
        try:
            storage_client = await Neo4jClient.create()
            repo = CommunityRepository(connector)
            community_writer = CommunityMutationWriter(storage_client)
            redis_client = get_thread_safe_sync_redis()

            # 批量预取所有用户的 MemoryConfig（tenant 与 model_id 同源），避免循环内逐个查库。
            # 加载失败的用户不存入 map，循环内检测到缺失时直接 skip。
            user_config_map: Dict[str, Any] = {}
            try:
                with get_db_context() as db:
                    from app.services.memory_agent_service import get_end_users_connected_configs_batch
                    from app.services.memory_config_service import MemoryConfigService
                    batch_configs = get_end_users_connected_configs_batch(end_user_ids, db)
                    for uid, cfg_info in batch_configs.items():
                        config_id = cfg_info.get("memory_config_id")
                        if config_id:
                            try:
                                user_config_map[uid] = MemoryConfigService(db).load_memory_config(config_id=config_id)
                            except Exception as e:
                                logger.error(f"[CommunityCluster] 用户 {uid} 加载配置失败，将跳过: {e}")
            except Exception as e:
                logger.error(f"[CommunityCluster] 批量获取配置失败: {e}")

            for requested_end_user_id in end_user_ids:
                write_lock = None
                end_user_id = requested_end_user_id
                try:
                    end_user_id, write_lock = _acquire_community_clustering_lock(
                        requested_end_user_id,
                        redis_client=redis_client,
                        expire=7200,
                    )

                    # 配置加载失败的用户直接跳过
                    memory_config = user_config_map.get(end_user_id)
                    if not memory_config and end_user_id != requested_end_user_id:
                        with get_db_context() as db:
                            from app.services.memory_agent_service import (
                                get_end_users_connected_configs_batch,
                            )
                            from app.services.memory_config_service import MemoryConfigService

                            resolved_configs = get_end_users_connected_configs_batch(
                                [end_user_id], db
                            )
                            config_info = resolved_configs.get(end_user_id) or {}
                            resolved_config_id = config_info.get("memory_config_id")
                            if resolved_config_id:
                                memory_config = MemoryConfigService(db).load_memory_config(
                                    config_id=resolved_config_id
                                )
                                user_config_map[end_user_id] = memory_config
                    if not memory_config:
                        failed += 1
                        logger.warning(
                            f"[CommunityCluster] 用户 {end_user_id} 无有效配置，跳过聚类"
                        )
                        continue

                    # 已有社区节点时，检查是否存在属性不完整的节点
                    has_communities = await repo.has_communities(end_user_id)
                    if has_communities:
                        incomplete_ids = await repo.get_incomplete_communities(
                            end_user_id,
                            check_embedding=bool(memory_config.embedding_model_id),
                        )
                        if not incomplete_ids:
                            skipped += 1
                            logger.debug(f"[CommunityCluster] 用户 {end_user_id} 社区节点均完整，跳过")
                            continue

                        # 对不完整的社区节点逐一补全元数据
                        engine = LabelPropagationEngine(
                            connector=connector,
                            memory_config=memory_config,
                            community_writer=community_writer,
                        )
                        logger.info(
                            f"[CommunityCluster] 用户 {end_user_id} 发现 {len(incomplete_ids)} 个属性不完整的社区，开始补全"
                        )
                        patch_ok = 0
                        patch_fail = 0
                        for cid in incomplete_ids:
                            try:
                                await engine._generate_community_metadata(
                                    [cid], end_user_id
                                )
                                patch_ok += 1
                            except Exception as patch_err:
                                patch_fail += 1
                                logger.error(f"[CommunityCluster] 社区 {cid} 元数据补全失败: {patch_err}")
                        logger.info(
                            f"[CommunityCluster] 用户 {end_user_id} 社区补全完成: 成功={patch_ok}, 失败={patch_fail}"
                        )
                        initialized += 1
                        continue

                    # 检查是否有 ExtractedEntity 节点
                    entities = await repo.get_all_entities(end_user_id)
                    if not entities:
                        skipped += 1
                        logger.debug(f"[CommunityCluster] 用户 {end_user_id} 无实体节点，跳过")
                        continue

                    # 每个用户使用自己的 MemoryConfig（tenant 与 model_id 同源）
                    engine = LabelPropagationEngine(
                        connector=connector,
                        memory_config=memory_config,
                        community_writer=community_writer,
                    )

                    logger.info(
                        f"[CommunityCluster] 用户 {end_user_id} 有 {len(entities)} 个实体，开始全量聚类，"
                        f"llm_model_id={memory_config.llm_model_id}")
                    await engine.full_clustering(end_user_id)
                    initialized += 1
                    logger.info(f"[CommunityCluster] 用户 {end_user_id} 聚类完成")

                except Exception as e:
                    failed += 1
                    logger.error(f"[CommunityCluster] 用户 {end_user_id} 聚类失败: {e}")
                finally:
                    if write_lock is not None:
                        write_lock.release()

        finally:
            try:
                if storage_client is not None:
                    await storage_client.close()
            finally:
                await connector.close()

        logger.info(
            f"[CommunityCluster] 任务完成: 初始化={initialized}, 跳过={skipped}, 失败={failed}"
        )
        return {
            "status": "SUCCESS",
            "initialized": initialized,
            "skipped": skipped,
            "failed": failed,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        # 全部失败（无初始化、无跳过）= 完全失败：raise → Celery FAILURE
        if result["failed"] > 0 and result["initialized"] == 0 and result["skipped"] == 0:
            raise RuntimeError(
                f"all {result['failed']} users failed in community clustering"
            )
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE；
    # 内层 _run() 中 connector.close() 已由 try/finally 保证释放。
    finally:
        _shutdown_loop_gracefully(loop)


# ─── User Metadata Extraction Task ───────────────────────────────────────────

# ──────────────────────────────────────────────
# 滑动窗口写入相关常量
# ──────────────────────────────────────────────

# Redis key 前缀
CONV_ACTIVE_KEY_PREFIX = "conv_active:"


@celery_app.task(
    bind=True,
    name="app.tasks.flush_conversation",
    queue="periodic_tasks",
    max_retries=0,
    acks_late=True,
)
def flush_conversation_task(self) -> None:
    """兜底写入任务（Beat 定时调度）。

    扫描所有空闲对话，逐个派发兜底写入任务。

    优先从 Redis Set (pending_conversations) 获取候选对话 ID，避免全表 JOIN 扫描。
    若 Set 不可用则回退到数据库查询。

    扫描条件（两者同时满足才派发）：
    1. 对话存在未写入消息（来自 Redis Set 或 DB 查询）
    2. Redis 中 conv_active:{conversation_id} 已过期或不存在（对话空闲 >5 分钟）

    dispatcher 内部逐条派发 write_message_task 并推进 write_cursor，
    下次扫描时 cursor 已推进不会重复触发，无需额外幂等锁。

    所有实际写入均收敛到 write_message_task 路径，由 memory_write 锁在 worker 侧保证串行。

    Fire-and-forget：异常时记录日志，不重试。
    """
    from sqlalchemy import func, select

    from app.core.memory.memory_service import MemoryService as _MS
    _dispatch_flush = _MS.dispatch_flush_conversation
    from app.models.conversation_model import Conversation

    # Ensure an event loop is available for awaiting the async dispatch function.
    _loop = set_asyncio_event_loop()

    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("[FlushScan] Redis 不可用，跳过本次扫描")
        return

    # 连接到 settings.REDIS_DB 的客户端，用于读取 conv_active key 和 pending_conversations Set
    active_redis_client = None
    try:
        active_redis_client = redis.StrictRedis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
            decode_responses=True,
        )
        active_redis_client.ping()
    except Exception as e:
        logger.warning(
            f"[FlushScan] 无法连接 conv_active 所在 Redis DB，"
            f"将跳过空闲检查（所有对话视为活跃）: err={e}"
        )
        active_redis_client = None

    dispatched = 0
    skipped_active = 0

    try:
        # 优先从 Redis Set 获取候选对话 ID
        candidate_conv_ids: list[str] | None = None
        if active_redis_client is not None:
            try:
                from app.core.memory.pipelines.dispatcher import PENDING_CONVERSATIONS_SET_KEY
                candidates = active_redis_client.smembers(PENDING_CONVERSATIONS_SET_KEY)
                if candidates:
                    candidate_conv_ids = list(candidates)
                    logger.info(f"[FlushScan] 从 Redis Set 获取 {len(candidate_conv_ids)} 个候选对话")
            except Exception as e:
                logger.warning(f"[FlushScan] 读取 pending_conversations Set 失败，回退到 DB 查询: {e}")

        # 回退：Redis Set 不可用或为空时，走数据库查询
        if candidate_conv_ids is None:
            with get_db_context() as db:
                from app.models.memory_message_model import MemoryMessage

                max_seq_subq = (
                    select(
                        MemoryMessage.conversation_id,
                        func.max(MemoryMessage.message_seq).label("max_seq"),
                    )
                    .where(MemoryMessage.conversation_id.isnot(None))
                    .group_by(MemoryMessage.conversation_id)
                    .subquery()
                )

                rows = (
                    db.execute(
                        select(Conversation.id)
                        .join(
                            max_seq_subq,
                            Conversation.id == max_seq_subq.c.conversation_id,
                        )
                        .where(max_seq_subq.c.max_seq > Conversation.write_cursor)
                    )
                    .scalars()
                    .all()
                )
                candidate_conv_ids = [str(r) for r in rows]

        logger.info(f"[FlushScan] 发现 {len(candidate_conv_ids)} 个对话存在未写入消息")

        # 过滤：确保对话所属 app 已存在已发布版本
        if candidate_conv_ids:
            try:
                from app.models.app_model import App

                with get_db_context() as db:
                    valid_conv_ids = [
                        str(cid) for cid in db.execute(
                            select(Conversation.id)
                            .join(App, App.id == Conversation.app_id)
                            .where(
                                Conversation.id.in_(candidate_conv_ids),
                                App.current_release_id.isnot(None),
                            )
                            .distinct()
                        ).scalars().all()
                    ]

                skipped_no_release = len(candidate_conv_ids) - len(valid_conv_ids)
                if skipped_no_release:
                    logger.info(f"[FlushScan] 跳过 {skipped_no_release} 个 app 未发布的对话")
                candidate_conv_ids = valid_conv_ids
            except Exception as e:
                logger.warning(f"[FlushScan] 过滤未发布 app 失败: err={e}")

        for conv_id_str in candidate_conv_ids:
            # 检查 conv_active key 是否存在（存在则对话仍活跃，跳过）
            if active_redis_client is not None:
                try:
                    active_key = f"{CONV_ACTIVE_KEY_PREFIX}{conv_id_str}"
                    if active_redis_client.exists(active_key):
                        skipped_active += 1
                        continue
                except Exception as e:
                    logger.warning(f"[FlushScan] 检查 conv_active 失败: conv={conv_id_str}, err={e}")
                    skipped_active += 1
                    continue
            else:
                skipped_active += 1
                continue

            # 派发单个对话的兜底写入
            try:
                _loop.run_until_complete(_dispatch_flush(conv_id_str))
                dispatched += 1
                logger.info(f"[FlushScan] 已处理: conv={conv_id_str}")
            except Exception as e:
                logger.error(f"[FlushScan] 处理失败: conv={conv_id_str}, err={e}", exc_info=True)

    except Exception as e:
        logger.error(f"[FlushScan] 扫描任务失败: err={e}", exc_info=True)
    finally:
        if active_redis_client is not None:
            try:
                active_redis_client.close()
            except Exception:
                pass

    logger.info(
        f"[FlushScan] 扫描完成: 处理={dispatched}, 跳过(活跃)={skipped_active}"
    )


@celery_app.task(name="app.tasks.scan_workflow_schedule_triggers", queue="periodic_tasks", time_limit=50,
                 soft_time_limit=45)
def scan_workflow_schedule_triggers():
    """扫描并派发已发布工作流中的定时触发器。"""
    from app.services.workflow_service import WorkflowService

    now = utcnow()
    triggered = 0

    with get_db_context() as db:
        service = WorkflowService(db)
        due_triggers = service.get_due_schedule_triggers(now)
        logger.info(f"[WorkflowSchedule] 扫描到 {len(due_triggers)} 个待执行触发器")

        for app, release, _config, trigger in due_triggers:
            trigger_id = trigger.get("id")
            try:
                run_workflow_schedule_trigger.apply_async(
                    kwargs={
                        "app_id": str(app.id),
                        "release_id": str(release.id),
                        "trigger_id": trigger_id,
                        "scheduled_at": to_iso_z(now),
                    },
                    queue="workflow_trigger_tasks",
                )
                runtime = {
                    **(trigger.get("runtime") or {}),
                    "dispatch_status": "queued",
                    "last_dispatched_at": to_iso_z(now),
                    "last_scheduled_at": to_iso_z(now),
                    "last_error": None,
                }
                service.update_release_trigger_runtime_state(release.id, trigger_id, runtime)
                service.update_trigger_runtime_state(app.id, trigger_id, runtime)
                triggered += 1
                logger.info(
                    f"[WorkflowSchedule] 已派发: app_id={app.id}, release_id={release.id}, trigger_id={trigger_id}"
                )
            except Exception as exc:
                logger.error(
                    f"[WorkflowSchedule] 派发失败: app_id={app.id}, trigger_id={trigger_id}, error={exc}",
                    exc_info=True,
                )

    return {"triggered": triggered, "scanned_at": to_iso_z(now)}


@celery_app.task(name="app.tasks.run_workflow_schedule_trigger", queue="workflow_trigger_tasks")
def run_workflow_schedule_trigger(app_id: str, release_id: str, trigger_id: str, scheduled_at: str | None = None):
    """执行单个已发布的 schedule trigger。"""
    from app.services.workflow_service import WorkflowService

    run_at = as_utc_aware(parse_iso_to_utc_naive(scheduled_at)) if scheduled_at else utcnow()
    with get_db_context() as db:
        service = WorkflowService(db)
        app = db.get(App, uuid.UUID(app_id))
        release = db.get(AppRelease, uuid.UUID(release_id))
        if not app or not release:
            logger.warning(
                f"[WorkflowSchedule] 跳过不存在的任务: app_id={app_id}, release_id={release_id}, trigger_id={trigger_id}"
            )
            return {"status": "skipped", "reason": "app_or_release_not_found"}

        if app.current_release_id != release.id:
            logger.info(
                f"[WorkflowSchedule] 跳过过期发布版本任务: "
                f"app_id={app_id}, queued_release_id={release_id}, current_release_id={app.current_release_id}, "
                f"trigger_id={trigger_id}"
            )
            return {"status": "skipped", "reason": "stale_release"}

        config = service._build_runtime_workflow_config_from_release(
            release,
            real_config_id=(app.workflow_config.id if app.workflow_config else None),
        )
        trigger = service._find_trigger_node(config.nodes, trigger_id=trigger_id, trigger_type="schedule")
        if not trigger:
            logger.warning(f"[WorkflowSchedule] 跳过不存在的 trigger: trigger_id={trigger_id}")
            return {"status": "skipped", "reason": "trigger_not_found"}

        runtime = trigger.get("runtime") or {}
        running_runtime = {
            **runtime,
            "dispatch_status": "running",
            "last_started_at": to_iso_z(utcnow()),
            "last_scheduled_at": to_iso_z(run_at),
            "last_error": None,
        }
        service.update_release_trigger_runtime_state(release.id, trigger_id, running_runtime)
        service.update_trigger_runtime_state(app.id, trigger_id, running_runtime)

        try:
            asyncio.run(
                service.invoke_schedule_trigger(
                    app=app,
                    release=release,
                    config=config,
                    trigger=trigger,
                    now=run_at,
                )
            )
            completed_runtime = {
                **running_runtime,
                "dispatch_status": "completed",
                "last_triggered_at": to_iso_z(run_at),
                "last_completed_at": to_iso_z(utcnow()),
                "last_error": None,
            }
            service.update_release_trigger_runtime_state(release.id, trigger_id, completed_runtime)
            service.update_trigger_runtime_state(app.id, trigger_id, completed_runtime)
            return {"status": "completed", "trigger_id": trigger_id, "scheduled_at": to_iso_z(run_at)}
        except Exception as exc:
            failed_runtime = {
                **running_runtime,
                "dispatch_status": "failed",
                "last_failed_at": to_iso_z(utcnow()),
                "last_error": str(exc),
            }
            service.update_release_trigger_runtime_state(release.id, trigger_id, failed_runtime)
            service.update_trigger_runtime_state(app.id, trigger_id, failed_runtime)
            logger.error(
                f"[WorkflowSchedule] 执行失败: app_id={app_id}, release_id={release_id}, trigger_id={trigger_id}, error={exc}",
                exc_info=True,
            )
            raise


@celery_app.task(name="app.tasks.draft_data_clean", queue="memory_tasks")
def draft_data_clean():
    import asyncio

    from app.core.memory.storage.custom import delete_end_user_memory_nodes

    with get_db_context() as db:
        stmt = select(EndUser.id).join(
            User,
            cast(User.id, String) == EndUser.other_id
        ).where(
            EndUser.is_active == True
        )
        result = db.execute(stmt)
        candidate_ids = list(result.scalars())

        if not candidate_ids:
            logger.info("draft_data_clean: 没有需要清理的终端用户")
            return {"deleted_count": 0}

        # Preserve the legacy cleanup invariant: deactivate the complete PG
        # batch first. Graph cleanup is best-effort afterwards, so a Neo4j or
        # Outbox failure must never leave these users active again.
        pg_deleted = (
            db.query(EndUser)
            .filter(
                EndUser.id.in_(candidate_ids),
                EndUser.is_active == True,
            )
            .update(
                {"is_active": False, "memory_count": 0},
                synchronize_session=False,
            )
        )
        db.commit()

    end_user_ids = [str(end_user_id) for end_user_id in candidate_ids]
    neo4j_deleted = 0
    neo4j_deleted_nodes = 0
    neo4j_failed = 0
    redis_client = get_thread_safe_sync_redis()
    for eid in end_user_ids:
        write_lock = RedisFairLock(
            key=f"memory_write:{eid}",
            redis_client=redis_client,
            expire=1200,
            timeout=60,
            auto_renewal=True,
        )
        try:
            with write_lock:
                deleted_nodes = asyncio.run(
                    delete_end_user_memory_nodes(eid)
                )
            neo4j_deleted += 1
            neo4j_deleted_nodes += deleted_nodes
        except Exception:
            neo4j_failed += 1
            logger.exception(
                "draft_data_clean: Neo4j/Outbox 删除失败，继续下一个用户 "
                "end_user_id=%s",
                eid,
            )
            continue

    logger.info(
        "draft_data_clean: PG 软删除 %s 个用户；Neo4j 成功 %s 组、%s 个节点，失败 %s 组",
        pg_deleted,
        neo4j_deleted,
        neo4j_deleted_nodes,
        neo4j_failed,
    )
    return {
        "pg_deleted": pg_deleted,
        "neo4j_deleted": neo4j_deleted,
        "neo4j_deleted_nodes": neo4j_deleted_nodes,
        "neo4j_failed": neo4j_failed,
    }


# ============================================================================
# Scene boundary and SceneSummary maintenance
# ============================================================================

_SCENE_IDLE_SCAN_CURSOR_KEY = "scene_summary:idle_scan_cursor:v1"
_SCENE_IDLE_SCAN_LOCK_KEY = "scene_summary:idle_scan_lock:v1"
_SCENE_IDLE_SCAN_LOCK_TTL_SECONDS = 300
_SCENE_IDLE_SCAN_CURSOR_TTL_SECONDS = 86400 * 30


@celery_app.task(
    bind=True,
    name="app.core.memory.generate_scene_summary",
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def generate_scene_summary(
    self,
    end_user_id: str,
    config_id: str,
    scene_start_message_id: str,
    close_reason: str,
    close_before_message_id: str | None = None,
    idle_high_watermark_message_id: str | None = None,
):
    from app.core.memory.scene.scene_summary_service import SceneSummaryService
    from app.schemas.scene_memory_schema import GenerateSceneSummaryTask

    payload = GenerateSceneSummaryTask(
        end_user_id=end_user_id,
        config_id=config_id,
        scene_start_message_id=scene_start_message_id,
        close_before_message_id=close_before_message_id,
        idle_high_watermark_message_id=idle_high_watermark_message_id,
        close_reason=close_reason,
    )
    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(SceneSummaryService().generate(payload))
        logger.info(
            "[SceneSummary] generation completed: scene_start=%s, "
            "close_reason=%s, status=%s, reason=%s, summary_id=%s",
            scene_start_message_id,
            close_reason,
            result.get("status"),
            result.get("reason"),
            result.get("summary_id"),
        )
        return result
    finally:
        _shutdown_loop_gracefully(loop)


@celery_app.task(name="app.tasks.scan_scene_summary_idle")
def scan_scene_summary_idle(
    limit: int = 100,
    batch_size: int = 200,
    scan_budget: int = 2000,
):
    """分批扫描静默 Scene，并派发 SceneSummary 生成任务。

    返回指标：
    - scanned：本轮实际检查的 stream 数量。
    - candidates：其中满足静默条件的 Scene 数量。
    - claimed：通过原子更新成功取得处理权的 Scene 数量。
    - dispatched：成功投递到 Celery 的摘要任务数量。
    - dispatch_failed：投递到 Celery 失败的任务数量。
    - exhausted：是否已经扫描到当前数据集末尾。
    - skipped_due_to_lock：是否因其他 scanner 正在运行而跳过本轮。
    """
    from app.repositories.memory_message_repository import MemoryMessageRepository

    redis_client = get_sync_redis_client()
    lock_token = uuid.uuid4().hex
    lock_acquired = False
    cursor_created_at = None
    cursor_id = None

    if redis_client is not None:
        try:
            lock_acquired = bool(
                redis_client.set(
                    _SCENE_IDLE_SCAN_LOCK_KEY,
                    lock_token,
                    nx=True,
                    ex=_SCENE_IDLE_SCAN_LOCK_TTL_SECONDS,
                )
            )
            if not lock_acquired:
                logger.info("[SceneSummary] idle scanner skipped: another scanner is running")
                return {
                    "scanned": 0,
                    "candidates": 0,
                    "claimed": 0,
                    "dispatched": 0,
                    "dispatch_failed": 0,
                    "skipped_due_to_lock": True,
                }
            raw_cursor = redis_client.get(_SCENE_IDLE_SCAN_CURSOR_KEY)
            if raw_cursor:
                cursor_payload = json.loads(raw_cursor)
                cursor_created_at = parse_iso_to_utc_naive(cursor_payload.get("created_at"))
                cursor_id = str(uuid.UUID(cursor_payload["id"]))
        except Exception as exc:
            logger.warning(
                "[SceneSummary] idle scanner Redis state unavailable; "
                "falling back to bounded scan from the beginning: %s",
                exc,
            )
            if lock_acquired:
                try:
                    redis_client.eval(
                        UNLOCK_SCRIPT,
                        1,
                        _SCENE_IDLE_SCAN_LOCK_KEY,
                        lock_token,
                    )
                except Exception:
                    pass
            redis_client = None
            lock_acquired = False

    try:
        with get_db_context() as db:
            repo = MemoryMessageRepository(db)
            scan_result = repo.list_idle_scene_candidates(
                limit=limit,
                batch_size=batch_size,
                scan_budget=scan_budget,
                after_created_at=cursor_created_at,
                after_id=cursor_id,
            )
            candidates = scan_result["candidates"]
            claimed = [
                candidate
                for candidate in candidates
                if repo.claim_scene_summary(
                    scene_start_message_id=candidate["scene_start_message_id"],
                    end_user_id=candidate["end_user_id"],
                )
            ]
            db.commit()

        if redis_client is not None:
            try:
                next_cursor = scan_result["next_cursor"]
                if scan_result["exhausted"] or next_cursor is None:
                    redis_client.delete(_SCENE_IDLE_SCAN_CURSOR_KEY)
                else:
                    redis_client.set(
                        _SCENE_IDLE_SCAN_CURSOR_KEY,
                        json.dumps({
                            "created_at": to_iso_z(next_cursor["created_at"]),
                            "id": next_cursor["id"],
                        }),
                        ex=_SCENE_IDLE_SCAN_CURSOR_TTL_SECONDS,
                    )
            except Exception as exc:
                logger.warning("[SceneSummary] idle scanner cursor update failed: %s", exc)

        dispatched = 0
        dispatch_failed = 0
        for candidate in claimed:
            try:
                async_result = generate_scene_summary.apply_async(
                    kwargs={
                        "end_user_id": candidate["end_user_id"],
                        "config_id": candidate["config_id"],
                        "scene_start_message_id": candidate["scene_start_message_id"],
                        "idle_high_watermark_message_id": candidate["idle_high_watermark_message_id"],
                        "close_reason": "IDLE_TIMEOUT",
                    }
                )
                dispatched += 1
                logger.info(
                    "[SceneSummary] idle task dispatched: scene_start=%s, task_id=%s",
                    candidate["scene_start_message_id"],
                    async_result.id,
                )
            except Exception:
                dispatch_failed += 1
                logger.exception(
                    "[SceneSummary] idle task dispatch failed, releasing claim: scene_start=%s",
                    candidate["scene_start_message_id"],
                )
                with get_db_context() as db:
                    MemoryMessageRepository(db).release_scene_summary_claim(
                        scene_start_message_id=candidate["scene_start_message_id"],
                        end_user_id=candidate["end_user_id"],
                    )
                    db.commit()
        logger.info(
            "[SceneSummary] idle scanner completed: scanned=%s, candidates=%s, "
            "claimed=%s, dispatched=%s, dispatch_failed=%s, exhausted=%s",
            scan_result["scanned"],
            len(candidates),
            len(claimed),
            dispatched,
            dispatch_failed,
            scan_result["exhausted"],
        )
        return {
            "scanned": scan_result["scanned"],
            "candidates": len(candidates),
            "claimed": len(claimed),
            "dispatched": dispatched,
            "dispatch_failed": dispatch_failed,
            "exhausted": scan_result["exhausted"],
            "skipped_due_to_lock": False,
        }
    finally:
        if redis_client is not None and lock_acquired:
            try:
                redis_client.eval(
                    UNLOCK_SCRIPT,
                    1,
                    _SCENE_IDLE_SCAN_LOCK_KEY,
                    lock_token,
                )
            except Exception as exc:
                logger.warning("[SceneSummary] idle scanner lock release failed: %s", exc)


@celery_app.task(
    name="app.tasks.consume_model_usage",
    bind=False,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=60,
    soft_time_limit=50,
)
def consume_model_usage_task() -> Dict[str, Any]:
    """定时任务：消费 model:usage 用量事件落 model_usage_records（spec §13.2）。

    每轮先领 idle > 60s 的遗留 pending，再按「批量 200 × 最多 20 批 / 10s」读新消息；
    beat 周期由 settings.MODEL_USAGE_CONSUME_INTERVAL_SECONDS（默认 5s）驱动。
    消费失败（Redis/PG 短暂不可用）不抛：消息留在 pending，下轮 XAUTOCLAIM 重领。
    """
    from app.services.usage_consumer import consume_model_usage

    try:
        return consume_model_usage()
    except Exception as exc:
        logger.warning(f"consume_model_usage 本轮失败（下轮重试）: {exc}", exc_info=True)
        return {"status": "RETRY_LATER", "error": str(exc)}
