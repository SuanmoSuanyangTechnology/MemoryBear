"""
MemoryWriteDispatcher — 记忆写入派发层

职责：
- 统一各入口点（API Service、Agent、Workflow、MCP、Flush）向 write_message_task 的派发逻辑
- 管理 memory_messages 表的消息写入
- 处理 Redis pending 集合和活跃 key（仅 agent/workflow 路径）
- 判断滑动窗口条件是否满足（仅 agent/workflow 路径）

写入路径分成两组：
1. **agent / workflow**：走 conversation_id 通道，产生 conversation 行，
   有 write_cursor、滑动窗口、Redis pending 集合、Flush 兜底。
2. **service_api / mcp**：无 conversation，写 memory_messages 时 conversation_id=NULL，
   用 (end_user_id, source) 作为分组键，写入后立即逐条派发任务。

各入口点保持原位置，通过本模块的函数进行统一派发。
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from app.core.memory.enums import MemoryMessageSource
from app.db import get_db_context, get_db_read
from app.repositories.memory_message_repository import MemoryMessageRepository

logger = logging.getLogger(__name__)

# 滑动窗口大小
WINDOW_SIZE = 3


# ──────────────────────────────────────────────
# Redis 活跃 key 管理（仅 agent/workflow 路径使用）
# ──────────────────────────────────────────────

CONV_ACTIVE_KEY_PREFIX = "conv_active:"
CONV_ACTIVE_TTL_SECONDS = 300
PENDING_CONVERSATIONS_SET_KEY = "pending_conversations"


async def refresh_active_key(conversation_id: str) -> None:
    """刷新对话活跃 key 的 TTL。"""
    try:
        from app.aioRedis import get_thread_safe_redis

        redis_client = get_thread_safe_redis()
        key = f"{CONV_ACTIVE_KEY_PREFIX}{conversation_id}"
        await redis_client.set(key, "1", ex=CONV_ACTIVE_TTL_SECONDS)
    except Exception as e:
        logger.warning(f"[Dispatcher] 刷新活跃 key 失败: conv={conversation_id}, err={e}")


# 模块级 Redis 客户端单例（连接 settings.REDIS_DB，供 mark/unmark/verify 使用）
_dispatcher_redis = None


def _get_dispatcher_redis():
    """获取连接到 settings.REDIS_DB 的 Redis 客户端（单例复用）。"""
    import redis as _redis
    from app.core.config import settings

    global _dispatcher_redis
    if _dispatcher_redis is None:
        try:
            _dispatcher_redis = _redis.StrictRedis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=5,
            )
        except Exception as e:
            logger.warning(f"[Dispatcher] 创建 Redis 客户端失败: {e}")
            return None
    return _dispatcher_redis


def mark_conversation_pending(conversation_id: str) -> None:
    """将对话 ID 加入 pending_conversations Redis Set。"""
    try:
        r = _get_dispatcher_redis()
        if r is not None:
            r.sadd(PENDING_CONVERSATIONS_SET_KEY, conversation_id)
    except Exception as e:
        logger.debug(f"[Dispatcher] mark_conversation_pending 失败: {e}")


def unmark_conversation_pending(conversation_id: str) -> None:
    """将对话 ID 从 pending_conversations Redis Set 中移除。"""
    try:
        r = _get_dispatcher_redis()
        if r is not None:
            r.srem(PENDING_CONVERSATIONS_SET_KEY, conversation_id)
    except Exception as e:
        logger.debug(f"[Dispatcher] unmark_conversation_pending 失败: {e}")


def verify_unmark_safe(conversation_id: str) -> bool:
    """在 unmark 前验证对话确实没有待写入消息。"""
    try:
        with get_db_read() as db:
            repo = MemoryMessageRepository(db)
            return repo.verify_cursor_complete(conversation_id)
    except Exception as e:
        logger.warning(f"[Dispatcher] verify_unmark_safe 失败，保守返回 False: conv={conversation_id}, err={e}")
        return False


# ──────────────────────────────────────────────
# 派发函数：各入口点使用
# ──────────────────────────────────────────────

def push_write_task(
    end_user_id: str,
    target_message: dict,
    context_before: List[dict],
    context_after: List[dict],
    config_id: str,
    workspace_id: str,
    conversation_id: str,
    message_seq: int,
    language: str = "zh",
    skip_cursor_advance: bool = False,
    source: str = "",
) -> str:
    """推送单条消息写入任务到 Celery。

    所有入口最终都通过这个函数派发任务。conversation_id 允许为空字符串
    （API/MCP 场景），下游 WritePipeline 已容忍空 conversation_id。

    Args:
        end_user_id: 终端用户 ID（分片键）
        target_message: 目标消息
        context_before: 上文消息列表
        context_after: 下文消息列表
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        conversation_id: 对话 ID；API/MCP 场景传空字符串
        message_seq: 消息序号
        language: 语言
        skip_cursor_advance: 是否跳过 cursor 推进（API/MCP 场景为 True）
        source: 写入来源（agent/service_api/mcp/workflow）

    Returns:
        任务 msg_id
    """
    from app.celery_task_scheduler import scheduler as celery_scheduler

    # 记录任务派发时刻，作为 pipeline 内 dialog_at 的第二级兜底
    dispatch_at = datetime.now(timezone.utc).isoformat()

    msg_id = celery_scheduler.push_task(
        "app.core.memory.agent.write_message",
        end_user_id,
        {
            "end_user_id": end_user_id,
            "target_message": target_message,
            "context_before": context_before,
            "context_after": context_after,
            "config_id": config_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id or "",
            "message_seq": message_seq,
            "language": language,
            "dispatch_at": dispatch_at,
            "skip_cursor_advance": skip_cursor_advance,
            "source": source,
        },
    )
    logger.info(
        f"[Dispatcher] 写入任务已推送: end_user={end_user_id}, "
        f"conv={conversation_id or '-'}, seq={message_seq}, msg_id={msg_id}"
    )
    return msg_id


# ──────────────────────────────────────────────
# 应用级记忆门禁检查
# ──────────────────────────────────────────────

async def check_memory_enabled(app_id: str) -> bool:
    """查询 app_releases.config -> 'memory' ->> 'enabled'。"""
    try:
        from sqlalchemy import select as sa_select
        from app.models.app_release_model import AppRelease

        with get_db_context() as db:
            result = db.execute(
                sa_select(AppRelease.config)
                .where(
                    AppRelease.app_id == uuid.UUID(str(app_id)),
                    AppRelease.is_active.is_(True),
                )
                .order_by(AppRelease.version.desc())
                .limit(1)
            ).scalar_one_or_none()

            config = result or {}
            memory_config = config.get("memory", {}) if isinstance(config, dict) else {}
            return bool(memory_config.get("enabled", False))
    except Exception as e:
        logger.warning(f"[Dispatcher] 检查 memory.enabled 失败: app={app_id}, err={e}")
        return False


# ──────────────────────────────────────────────
# RAG 写入
# ──────────────────────────────────────────────


async def write_messages_to_rag(
    messages: List[dict],
    end_user_id: str,
    user_rag_memory_id: str,
) -> None:
    """将 messages 拼接为文本并写入 RAG 存储。"""
    from app.services.memory_konwledges_server import write_rag

    message_text = "\n".join([
        f"{(msg['role'] if isinstance(msg, dict) else msg.role)}: "
        f"{(msg['content'] if isinstance(msg, dict) else msg.content)}"
        for msg in messages
    ])
    await write_rag(end_user_id, message_text, user_rag_memory_id)


# ──────────────────────────────────────────────
# 入口1: API Service Async (/writer_service_async)
# ──────────────────────────────────────────────

async def dispatch_api_service_async(
    messages: List[dict],
    end_user_id: str,
    config_id: str,
    workspace_id: str,
    language: str = "zh",
) -> List[str]:
    """API Service 异步写入入口（仅写 memory_messages，无 conversation）。

    流程（对应设计文档 §3.5）：
    1. 批量写入 memory_messages 表（conversation_id=NULL, source=service_api）
    2. 逐条对 user 消息派发 WritePipeline 任务，邻近 assistant 消息作为上下文

    API 写入的 role 不确定——可能 user 连续、assistant 连续、也可能交替。
    此处为每条 user 消息构建滑动窗口上下文（向前/后各找 WINDOW_SIZE 个 user 消息
    范围内的所有消息，包含中间穿插的 assistant），确保 WritePipeline 能获取充分信息。

    Returns:
        派发的任务 ID 列表
    """
    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        written_mms = repo.write_batch(
            conversation_id=None,
            messages=messages,
            end_user_id=end_user_id,
            source=MemoryMessageSource.SERVICE_API,
        )
        db.commit()

    if not written_mms:
        return []

    # 预计算上下文窗口：找出所有 user 消息索引，为每条 user 构建 context_before/after
    task_ids: List[str] = []
    user_indices = [i for i, m in enumerate(written_mms) if m["role"] == "user"]

    for p, idx in enumerate(user_indices):
        msg = written_mms[idx]

        # 上文：向前找最多 WINDOW_SIZE 个 user 消息，取最早那个的位置作为起点
        before_user_p = max(0, p - WINDOW_SIZE)
        before_start = user_indices[before_user_p]
        context_before = written_mms[before_start:idx]

        # 下文：向后找最多 WINDOW_SIZE 个 user 消息，取最晚那个的下一位作为终点
        after_user_p = min(len(user_indices) - 1, p + WINDOW_SIZE)
        if after_user_p == p:
            # 当前是最后一条 user（或窗口内没有更后面的 user），取到列表末尾
            context_after = written_mms[idx + 1:]
        else:
            after_end = user_indices[after_user_p] + 1
            context_after = written_mms[idx + 1:after_end]

        msg_id = push_write_task(
            end_user_id=end_user_id,
            target_message=msg,
            context_before=context_before,
            context_after=context_after,
            config_id=config_id,
            workspace_id=workspace_id,
            conversation_id="",  # API 场景无 conversation
            message_seq=msg["message_seq"],
            language=language,
            skip_cursor_advance=True,
            source=MemoryMessageSource.SERVICE_API.value,
        )
        task_ids.append(msg_id)

    return task_ids


# ──────────────────────────────────────────────
# 入口2: Agent 消息摄入（走 conversation_id 通道）
# ──────────────────────────────────────────────


async def ingest_agent_message(
    conversation_id: str,
    message: "Any",
    app_id: str,
    config_id: str = "",
    workspace_id: str = "",
    end_user_id: str = "",
    should_memorize: bool = True,
    language: str = "zh",
) -> bool:
    """Agent 消息摄入：写入 memory_messages 表 + 触发滑动窗口派发。

    Returns:
        True 表示成功写入，False 表示跳过（门禁未开或写入失败）
    """

    if not await check_memory_enabled(app_id):
        return False

    files = None
    if hasattr(message, "meta_data") and message.meta_data:
        files = message.meta_data.get("files")

    # Agent 路径：用 message.created_at 作为 dialog_at，语义上是对话真实发生的时间
    dialog_at: Optional[str] = None
    if hasattr(message, "created_at") and message.created_at:
        _created = message.created_at
        if isinstance(_created, datetime):
            _created = _created.replace(tzinfo=timezone.utc) if _created.tzinfo is None else _created
            dialog_at = _created.isoformat()
        elif isinstance(_created, str):
            dialog_at = _created

    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        written = repo.write_batch(
            conversation_id=str(conversation_id),
            messages=[{
                "role": message.role,
                "content": message.content,
                "original_message_id": message.id,
                "created_at": message.created_at,
                "should_memorize": should_memorize,
                "files": files,
                "dialog_at": dialog_at,
            }],
            end_user_id=end_user_id,
            source=MemoryMessageSource.AGENT,
        )
        if not written:
            return False
        db.commit()

    await refresh_active_key(conversation_id)
    mark_conversation_pending(conversation_id)

    await check_sliding_window_and_dispatch(
        conversation_id=str(conversation_id),
        config_id=config_id,
        end_user_id=end_user_id,
        workspace_id=workspace_id,
        language=language,
    )
    return True


# ──────────────────────────────────────────────
# 入口3: Workflow 消息摄入
# ──────────────────────────────────────────────


async def ingest_workflow_messages(
    messages: List[dict],
    conversation_id: str,
    end_user_id: str,
    config_id: str,
    workspace_id: str,
    language: str = "zh",
) -> None:
    """Workflow 消息摄入：批量写入 memory_messages 表 + 触发滑动窗口派发。

    MemoryWriteNode 仅存在于对话流 workflow 中，执行前已由 create_or_get_conversation
    在 conversations 表创建真实会话，sys.conversation_id 始终有效（与 MemoryReadNode
    的假设一致）。pure_workflow（策略工作流）没有记忆存储节点、无会话，不会走到这里。
    因此直接走 conversation 通道，无需空值守卫或哨兵兜底。
    """
    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        repo.write_batch(
            conversation_id=conversation_id,
            messages=messages,
            end_user_id=end_user_id,
            source=MemoryMessageSource.WORKFLOW,
        )
        db.commit()

    await refresh_active_key(conversation_id)
    mark_conversation_pending(conversation_id)

    await check_sliding_window_and_dispatch(
        conversation_id=conversation_id,
        config_id=config_id,
        end_user_id=end_user_id,
        workspace_id=workspace_id,
        language=language,
    )


# ──────────────────────────────────────────────
# 入口4: Flush 兜底任务（仅服务 agent/workflow 路径）
#
# API/MCP 消息 conversation_id=NULL，JOIN conversations 天然扫不到，
# 无需 flush 兜底；本函数只处理 agent/workflow 的未派发消息。
# ──────────────────────────────────────────────


def _resolve_memory_config_id(conversation_id: str) -> "uuid.UUID | None":
    """从 conversation 所属 workspace 获取默认记忆配置 ID。

    查询链路：conversations.workspace_id → get_workspace_memory_config_id(workspace_id)
    与滑动窗口路径（conversation_service → MemoryConfigService.get_workspace_active_config_id）
    使用同一个底层函数，确保同一会话的所有消息使用相同的记忆配置。
    """
    try:
        from sqlalchemy import select as sa_select
        from app.models.conversation_model import Conversation
        from app.repositories.workspace_repository import get_workspace_memory_config_id

        with get_db_context() as db:
            workspace_id = db.execute(
                sa_select(Conversation.workspace_id)
                .where(Conversation.id == conversation_id)
            ).scalar_one_or_none()

            if workspace_id is None:
                return None

            return get_workspace_memory_config_id(db, workspace_id)
    except Exception as e:
        logger.error(f"[Dispatcher] 解析 workspace memory_config 异常: conv={conversation_id}, err={e}", exc_info=True)
        return None


def dispatch_flush_conversation(conversation_id: str) -> int:
    """Flush 兜底任务派发：处理单个对话的所有未写入消息。

    仅服务 agent/workflow 路径。API/MCP 消息 conversation_id=NULL 不会被扫到。
    只派发 role=user + should_memorize=TRUE 的消息，其余直接推进 cursor。
    """
    from sqlalchemy import select as sa_select
    from app.models.conversation_model import Conversation
    from app.models.memory_message_model import MemoryMessage

    try:
        # Step 1: 查询对话信息 + 未写入消息
        with get_db_context() as db:
            row = db.execute(
                sa_select(
                    Conversation.write_cursor,
                    Conversation.user_id,
                    Conversation.workspace_id,
                ).where(Conversation.id == conversation_id)
            ).one_or_none()

            if row is None:
                logger.warning(f"[Dispatcher] Flush 对话不存在: conv={conversation_id}")
                return 0

            write_cursor, end_user_id, workspace_id = row
            end_user_id = str(end_user_id) if end_user_id else ""
            workspace_id = str(workspace_id) if workspace_id else ""

            if not end_user_id:
                logger.warning(f"[Dispatcher] Flush end_user_id 为空，跳过: conv={conversation_id}")
                return 0

            pending_messages = [
                {
                    "message_seq": msg.message_seq,
                    "role": msg.role,
                    "should_memorize": msg.should_memorize,
                }
                for msg in db.execute(
                    sa_select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq > (write_cursor or 0),
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                ).scalars().all()
            ]

            if not pending_messages:
                logger.info(f"[Dispatcher] Flush 无未写入消息，跳过: conv={conversation_id}")
                return 0

        # Step 2: 解析 memory_config_id（走 workspace 默认配置，与滑动窗口路径一致）
        config_id_resolved = _resolve_memory_config_id(conversation_id)
        if not config_id_resolved:
            logger.warning(f"[Dispatcher] Flush 未能解析 memory_config_id，跳过: conv={conversation_id}")
            return 0
        config_id = str(config_id_resolved)

        # Step 3: 逐条处理
        dispatched = 0
        skipped_non_user = 0  # 因角色不是 user 或 should_memorize=False 被跳过（只推进游标）
        for msg in pending_messages:
            target_seq = msg["message_seq"]

            if msg["role"] != "user" or not msg["should_memorize"]:
                with get_db_context() as db:
                    MemoryMessageRepository(db).advance_write_cursor(conversation_id, target_seq)
                    db.commit()
                skipped_non_user += 1
                continue

            if dispatch_single_message(
                conversation_id=conversation_id,
                target_seq=target_seq,
                end_user_id=end_user_id,
                config_id=config_id,
                workspace_id=workspace_id,
            ):
                dispatched += 1

        # Step 4: 清理 pending_conversations Set
        if verify_unmark_safe(conversation_id):
            unmark_conversation_pending(conversation_id)

        logger.info(
            f"[Dispatcher] Flush 已派发 {dispatched} 个 user 消息任务: "
            f"conv={conversation_id}, total_pending={len(pending_messages)}, "
            f"skipped_non_user={skipped_non_user}"
        )
        return dispatched

    except Exception as e:
        logger.error(f"[Dispatcher] Flush 失败: conv={conversation_id}, err={e}", exc_info=True)
        return 0

# ──────────────────────────────────────────────
# 滑动窗口派发决策
# ──────────────────────────────────────────────


async def check_sliding_window_and_dispatch(
    conversation_id: str,
    config_id: str,
    end_user_id: str,
    workspace_id: str,
    language: str = "zh",
) -> None:
    """滑动窗口条件检查 + 派发（agent/workflow 路径）。

    检查 pending user 消息下文是否 ≥ WINDOW_SIZE，满足则构建上下文并 push_write_task。
    一次只派发一条。
    """
    from bisect import bisect_right

    from app.repositories.memory_message_repository import message_to_dict

    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        write_cursor = repo.get_write_cursor(conversation_id) or 0
        pending = repo.get_pending_messages(conversation_id, write_cursor)
        pending_dicts = [message_to_dict(m) for m in pending]

    if not pending_dicts:
        return

    # 取所有 user 消息的 seq 列表（用于计算下文条数）
    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        all_user_seqs: List[int] = repo.get_user_seqs(conversation_id)

    for msg in pending_dicts:
        # 滑动窗口路径只负责派发 user 消息的写入任务，cursor 只推进到 user 消息的 seq。
        # non-user 消息（assistant）不推进 cursor，跳过继续往后找第一条待派发的 user 消息。
        # assistant 的 cursor 推进完全交给 flush 兜底路径处理。
        if msg.get("role") != "user" or not msg.get("should_memorize", True):
            continue

        target_seq = msg["message_seq"]

        # 检查下文条数是否满足窗口要求
        downstream_count = len(all_user_seqs) - bisect_right(all_user_seqs, target_seq)
        if downstream_count < WINDOW_SIZE:
            logger.info(
                f"[WriteDispatcher] 下文不足，等待更多消息: conv={conversation_id}, "
                f"seq={target_seq}, downstream={downstream_count} < {WINDOW_SIZE}"
            )
            return

        # 先推进 cursor，确保同一条消息不会被后续调用（flush 或下次 ingest）重复派发。
        # advance_write_cursor 使用 WHERE write_cursor < seq，具有原子性保护。
        with get_db_context() as db:
            repo = MemoryMessageRepository(db)
            acquired = repo.advance_write_cursor(conversation_id, target_seq)
            db.commit()

        if not acquired:
            # cursor 已被推进（该消息已被其他路径处理），跳过
            logger.info(
                f"[WriteDispatcher] cursor 已被推进，跳过重复派发: "
                f"conv={conversation_id}, seq={target_seq}"
            )
            return

        # 构建上下文窗口
        with get_db_context() as db:
            repo = MemoryMessageRepository(db)
            context_before = [message_to_dict(m) for m in repo.build_context_before(conversation_id, target_seq)]
            context_after = [message_to_dict(m) for m in repo.build_context_after(conversation_id, target_seq)]

        # 派发写入任务
        push_write_task(
            end_user_id=end_user_id,
            target_message=msg,
            context_before=context_before,
            context_after=context_after,
            config_id=config_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_seq=target_seq,
            language=language,
            source=MemoryMessageSource.AGENT.value,
        )

        # 一次只派发一条
        return


def dispatch_single_message(
    conversation_id: str,
    target_seq: int,
    end_user_id: str,
    config_id: str,
    workspace_id: str,
) -> bool:
    """为单条 user 消息构建上下文并派发写入任务（Flush 路径使用）。"""
    from app.repositories.memory_message_repository import message_to_dict

    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        target_orm = repo.get_by_seq(conversation_id, target_seq)
        if target_orm is None:
            return False
        msg_dict = message_to_dict(target_orm)
        context_before = [message_to_dict(m) for m in repo.build_context_before(conversation_id, target_seq)]
        context_after = [message_to_dict(m) for m in repo.build_context_after(conversation_id, target_seq)]

    # 先推进 cursor，防止并发 flush 重复派发同一条消息
    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        acquired = repo.advance_write_cursor(conversation_id, target_seq)
        db.commit()

    if not acquired:
        logger.info(
            f"[WriteDispatcher] cursor 已被推进，跳过重复派发: "
            f"conv={conversation_id}, seq={target_seq}"
        )
        return False

    push_write_task(
        end_user_id=end_user_id,
        target_message=msg_dict,
        context_before=context_before,
        context_after=context_after,
        config_id=config_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        message_seq=target_seq,
        source=MemoryMessageSource.AGENT.value,
    )

    return True


# ──────────────────────────────────────────────
# 入口5: MCP 写入（无 conversation，直接派发）
# ──────────────────────────────────────────────


async def dispatch_mcp_write(
    message: str,
    end_user_id: str,
    config_id: uuid.UUID,
    workspace_id: str,
    dialog_at: str = "",
) -> str:
    """MCP 写入入口。

    MCP 每次仅写入单条 user message，不需要上下文窗口。
    流程（对应设计文档 §3.5）：
    1. 写入 memory_messages 表（conversation_id=NULL, source=mcp, end_user_id=X）
    2. 直接派发写入任务（context_before=[], context_after=[]）

    Args:
        message: 用户消息内容
        end_user_id: 终端用户 ID
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        dialog_at: 对话发生时间（ISO 8601）

    Returns:
        派发的任务 msg_id
    """
    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        written = repo.write_batch(
            conversation_id=None,
            messages=[{"role": "user", "content": message, "dialog_at": dialog_at}],
            end_user_id=end_user_id,
            source=MemoryMessageSource.MCP,
        )
        db.commit()

    if not written:
        return ""

    target_msg = written[0]
    target_seq = target_msg["message_seq"]

    msg_id = push_write_task(
        end_user_id=end_user_id,
        target_message=target_msg,
        context_before=[],
        context_after=[],
        config_id=str(config_id),
        workspace_id=workspace_id,
        conversation_id="",  # MCP 无 conversation
        message_seq=target_seq,
        skip_cursor_advance=True,
        source=MemoryMessageSource.MCP.value,
    )

    logger.info(
        f"[Dispatcher] MCP 写入任务已推送: end_user={end_user_id}, "
        f"source=mcp, seq={target_seq}, msg_id={msg_id}"
    )
    return msg_id
