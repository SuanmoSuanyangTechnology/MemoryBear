"""
MemoryWriteDispatcher — 记忆写入派发层

职责：
- 统一各入口点（API Service、Agent、Workflow、Flush）向 write_message_task 的派发逻辑
- 管理 memory_messages 表的消息写入
- 处理 Redis pending 集合和活跃 key
- 判断滑动窗口条件是否满足

各入口点保持原位置，通过本模块的函数进行统一派发。
"""

import logging
import uuid
from typing import Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.conversation_model import Message

from app.db import get_db_context, get_db_read
from app.repositories.memory_message_repository import MemoryMessageRepository

logger = logging.getLogger(__name__)

# 滑动窗口大小
WINDOW_SIZE = 3


# ──────────────────────────────────────────────
# Redis 活跃 key 管理
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
# Conversation 管理
# ──────────────────────────────────────────────

# 全局哨兵 App ID（Service API 虚拟会话专用）
SENTINEL_APP_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_sentinel_app_verified: bool = False


def _ensure_sentinel_app_exists() -> None:
    """确保哨兵 App 在 apps 表中存在。"""
    global _sentinel_app_verified
    if _sentinel_app_verified:
        return

    from app.models.app_model import App

    try:
        with get_db_context() as db:
            existing = db.get(App, SENTINEL_APP_ID)
            if existing is not None:
                _sentinel_app_verified = True
                return

            sentinel = App(
                id=SENTINEL_APP_ID,
                workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
                created_by=uuid.UUID("00000000-0000-0000-0000-000000000000"),
                name="__system_memory_service__",
                type="agent",
                visibility="private",
                status="active",
                is_active=True,
            )
            db.add(sentinel)
            db.commit()
            _sentinel_app_verified = True
    except Exception:
        _sentinel_app_verified = True


def get_or_create_service_api_conversation(
    workspace_id: str,
    end_user_id: str,
) -> str:
    """按 (workspace_id, end_user_id, app_id=SENTINEL) 查找或创建虚拟会话。"""
    from app.models.conversation_model import Conversation

    _ensure_sentinel_app_exists()

    try:
        _ws_id = uuid.UUID(workspace_id)
    except (ValueError, AttributeError) as e:
        raise ValueError(f"workspace_id 格式非法: {workspace_id!r}") from e

    with get_db_context() as db:
        conv = (
            db.query(Conversation)
            .filter(
                Conversation.workspace_id == _ws_id,
                Conversation.app_id == SENTINEL_APP_ID,
                Conversation.user_id == end_user_id,
            )
            .first()
        )

        if conv:
            return str(conv.id)

        conv = Conversation(
            id=uuid.uuid4(),
            app_id=SENTINEL_APP_ID,
            workspace_id=_ws_id,
            user_id=end_user_id,
            is_draft=True,
            write_cursor=0,
        )
        db.add(conv)
        db.commit()
        return str(conv.id)


async def ensure_conversation_exists(
    conversation_id: str,
    workspace_id: str = "",
) -> None:
    """确保 conversations 表中存在该记录。"""
    from app.models.conversation_model import Conversation

    try:
        with get_db_context() as db:
            existing = db.get(Conversation, uuid.UUID(conversation_id))
            if existing is not None:
                return

            _ws_id = uuid.UUID(workspace_id) if workspace_id else uuid.UUID("00000000-0000-0000-0000-000000000000")

            conv = Conversation(
                id=uuid.UUID(conversation_id),
                app_id=SENTINEL_APP_ID,
                workspace_id=_ws_id,
                is_draft=True,
            )
            db.add(conv)
            db.commit()
    except Exception as e:
        logger.warning(f"[Dispatcher] ensure_conversation_exists 失败: conv={conversation_id}, err={e}")


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
) -> str:
    """推送单条消息写入任务到 Celery。

    所有入口最终都通过这个函数派发任务。

    Args:
        end_user_id: 终端用户 ID（分片键）
        target_message: 目标消息
        context_before: 上文消息列表
        context_after: 下文消息列表
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        conversation_id: 对话 ID
        message_seq: 消息序号
        language: 语言

    Returns:
        任务 msg_id
    """
    from app.celery_task_scheduler import scheduler as celery_scheduler

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
            "conversation_id": conversation_id,
            "message_seq": message_seq,
            "language": language,
        },
    )
    logger.info(
        f"[Dispatcher] 写入任务已推送: end_user={end_user_id}, "
        f"conv={conversation_id}, seq={message_seq}, msg_id={msg_id}"
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
    """API Service 异步写入入口。

    1. 获取/创建虚拟 conversation
    2. 批量写入 memory_messages 表
    3. 逐条派发 user 消息的写入任务

    Returns:
        派发的任务 ID 列表
    """
    # 1. 获取/创建虚拟 conversation
    conversation_id = get_or_create_service_api_conversation(
        workspace_id=workspace_id,
        end_user_id=end_user_id,
    )

    # 2. 批量写入 memory_messages 表
    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        written_mms = repo.write_batch(conversation_id, messages)
        db.commit()

    if not written_mms:
        return []

    # 3. 预计算上下文窗口并逐条派发
    task_ids = []
    user_indices = [i for i, m in enumerate(written_mms) if m["role"] == "user"]

    # 先推进 cursor 到 max_seq，防止 flush 路径在 push_write_task 期间扫到这批消息重复派发
    max_seq = max(mm["message_seq"] for mm in written_mms)
    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        repo.advance_write_cursor(conversation_id, max_seq)
        db.commit()

    for p, idx in enumerate(user_indices):
        msg = written_mms[idx]

        # 上文：向前找最多 WINDOW_SIZE 个 user
        before_user_p = max(0, p - WINDOW_SIZE)
        before_start = user_indices[before_user_p]
        context_before = written_mms[before_start:idx]

        # 下文：向后找最多 WINDOW_SIZE 个 user
        after_user_p = min(len(user_indices) - 1, p + WINDOW_SIZE)
        if after_user_p == p:
            # 当前是最后一条 user 消息，context_after 取到列表末尾（包含尾部 assistant 消息）
            context_after = written_mms[idx + 1:]
        else:
            after_end = user_indices[after_user_p] + 1
            context_after = written_mms[idx + 1:after_end]

        # 派发任务
        msg_id = push_write_task(
            end_user_id=end_user_id,
            target_message=msg,
            context_before=context_before,
            context_after=context_after,
            config_id=config_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_seq=msg["message_seq"],
            language=language,
        )
        task_ids.append(msg_id)

    return task_ids


# ──────────────────────────────────────────────
# 入口2: Agent 消息摄入
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

    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        memory_msg = repo.write_single(
            conversation_id=str(conversation_id),
            original_message_id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            should_memorize=should_memorize,
            files=files,
        )
        if memory_msg is None:
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
    """Workflow 消息摄入：批量写入 memory_messages 表 + 触发滑动窗口派发。"""

    await ensure_conversation_exists(conversation_id, workspace_id)

    with get_db_context() as db:
        repo = MemoryMessageRepository(db)
        repo.write_batch(conversation_id, messages)
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
# 入口4: Flush 兜底任务
#
# 调用链路：
#   dispatch_flush_conversation (扫描待处理消息)
#     ├── _resolve_release_memory_config_id (从 app_releases 解析 config_id)
#     └── dispatch_single_message [write_dispatcher] (构建上下文 + push_write_task + 推进 cursor)
#           └── push_write_task (发 Celery 任务)
# ──────────────────────────────────────────────


def _resolve_release_memory_config_id(conversation_id: str) -> "uuid.UUID | None":
    """从应用当前发布版本的 config 中解析 memory_config_id。

    查询链路：conversations.app_id → apps.current_release_id → app_releases.config["memory"]["memory_config_id"]
    """
    try:
        from sqlalchemy import select as sa_select
        from app.models.app_model import App
        from app.models.app_release_model import AppRelease
        from app.models.conversation_model import Conversation
        from app.services.memory_config_service import MemoryConfigService

        with get_db_context() as db:
            row = db.execute(
                sa_select(
                    App.id,
                    App.type,
                    App.current_release_id,
                    AppRelease.config,
                )
                .select_from(Conversation)
                .join(App, App.id == Conversation.app_id)
                .outerjoin(AppRelease, AppRelease.id == App.current_release_id)
                .where(Conversation.id == conversation_id)
            ).one_or_none()

            if row is None:
                return None

            app_id, app_type, current_release_id, release_config = row

            if not current_release_id:
                return None

            if not isinstance(release_config, dict) or not release_config:
                return None

            config_id, _ = MemoryConfigService(db).extract_memory_config_id(
                app_type=str(app_type) if app_type else "",
                config=release_config,
            )
            return config_id
    except Exception as e:
        logger.error(f"[Dispatcher] 解析 release memory_config_id 异常: conv={conversation_id}, err={e}", exc_info=True)
        return None


def dispatch_flush_conversation(conversation_id: str) -> int:
    """Flush 兜底任务派发：处理单个对话的所有未写入消息。

    只派发 role=user + should_memorize=TRUE 的消息，其余直接推进 cursor。

    Args:
        conversation_id: 对话 ID

    Returns:
        派发的任务数
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

        # Step 2: 解析 memory_config_id
        config_id = ""
        release_config_id = _resolve_release_memory_config_id(conversation_id)
        if not release_config_id:
            logger.warning(f"[Dispatcher] Flush 未能解析 memory_config_id，跳过: conv={conversation_id}")
            return 0
        config_id = str(release_config_id)

        # Step 3: 逐条处理
        dispatched = 0
        skipped_non_user = 0 # 统计在 Flush 流程中因角色不是 user 或 should_memorize=False 而被跳过（只推进游标、不派发写入任务）的消息数量
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
    """滑动窗口条件检查 + 派发。

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
    """为单条 user 消息构建上下文并派发写入任务（Flush 路径使用）。

    Returns:
        True 表示成功派发，False 表示消息不存在
    """
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
    )

    return True
