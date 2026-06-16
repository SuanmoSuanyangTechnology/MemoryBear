"""
消息摄入模块 — Agent 对话消息同步到 memory_messages 表

职责：
- 检查应用级记忆门禁（memory.enabled）
- 将消息写入 memory_messages 表
- 刷新 Redis 活跃 key
- 分派给 SlidingWindowScheduler
"""

import logging
import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models.conversation_model import Message
    from sqlalchemy.orm import Session

from app.db import get_db_context
from app.models.memory_message_model import MemoryMessage

logger = logging.getLogger(__name__)

# Redis key 前缀（与 app.tasks.CONV_ACTIVE_KEY_PREFIX 保持一致）
CONV_ACTIVE_KEY_PREFIX = "conv_active:"
# 对话活跃 key 的 TTL（秒）。每写入一条 memory_messages 都会 SETEX 续期，
# 超过该时长无新消息后 flush_conversation_task 扫描模式会派发兜底写入
CONV_ACTIVE_TTL_SECONDS = 300


def extract_files_from_message(message: "Message") -> Optional[list]:
    """从 Message ORM 对象的 meta_data 中提取 files 信息。

    Agent 对话场景中，文件信息存储在 messages.meta_data["files"] 字段中，
    格式为 [{"type": "image", "url": "...", ...}, ...]。

    Args:
        message: Message ORM 对象

    Returns:
        文件信息列表，若无文件则返回 None
    """
    if not message.meta_data:
        return None
    files = message.meta_data.get("files")
    if not files:
        return None
    return files


def persist_memory_message(
    conversation_id: str,
    original_message_id,
    role: str,
    content: str,
    created_at,
    should_memorize: bool = True,
    files: Optional[list] = None,
) -> Optional["MemoryMessage"]:
    """在事务内自增 message_seq 并写入 memory_messages 表。

    message_seq 完全由 memory_messages 自身决定，与 messages 表的序号无关——
    memory.enabled=false 时消息只进 messages 表不进 memory_messages，两表序号
    本就不应一一对应。

    Args:
        conversation_id: 会话 ID（字符串）
        original_message_id: 原始 messages 表行的 id（用于反查源消息）
        role: user/assistant/system
        content: 消息内容
        created_at: 时间戳，沿用 Message 的 created_at 以保持时间一致
        should_memorize: 是否触发 Write_Pipeline；False 时仍写候选池但 cursor
            只推进不萃取（用于"用户在会话里关闭记忆开关"场景）
        files: 多模态文件信息列表（FileInput dict 格式），可为 None

    Returns:
        写入成功的 MemoryMessage 实例；失败时返回 None
    """
    from sqlalchemy import func, select as sa_select

    try:
        with get_db_context() as db:
            max_seq = db.execute(
                sa_select(func.coalesce(func.max(MemoryMessage.message_seq), 0))
                .where(MemoryMessage.conversation_id == uuid.UUID(conversation_id))
            ).scalar()
            next_seq = (max_seq or 0) + 1

            memory_msg = MemoryMessage(
                id=uuid.uuid4(),
                conversation_id=uuid.UUID(conversation_id),
                original_message_id=original_message_id,
                role=role,
                content=content,
                message_seq=next_seq,
                should_memorize=should_memorize,
                created_at=created_at,
                files=files,
            )
            db.add(memory_msg)
            db.commit()
            logger.debug(
                f"[MessageIngestion] MemoryMessage 已写入: "
                f"conv={conversation_id}, seq={next_seq}, role={role}, "
                f"should_memorize={should_memorize}"
            )

            # 标记对话有待处理消息，供 scan_idle 快速过滤
            from app.core.memory.sliding_window.window_utils import mark_conversation_pending
            mark_conversation_pending(conversation_id)

            return memory_msg
    except Exception as e:
        logger.error(
            f"[MessageIngestion] 写入 memory_messages 失败: "
            f"conv={conversation_id}, err={e}",
            exc_info=True,
        )
        return None


async def check_memory_enabled(app_id: str) -> bool:
    """查询 app_releases.config -> 'memory' ->> 'enabled'。

    只读应用当前发布版本（is_active=True 且最新）的配置——这是产品规则：
    agent 应用必须发布之后配置才生效；未发布的应用不写入候选池。

    返回 False 若：
      - 应用未发布（无 is_active=True 的记录）
      - 配置中无 memory 键
      - memory.enabled = false

    Args:
        app_id: 应用 ID

    Returns:
        True 表示该应用启用了记忆功能（已发布且 memory.enabled=true）
    """
    try:
        from sqlalchemy import select as sa_select
        from app.models.app_release_model import AppRelease

        with get_db_context() as db:
            # 只读最新发布版本，按版本号倒序避免 MultipleResultsFound
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
        logger.warning(
            f"[MessageIngestion] 检查 memory.enabled 失败，默认返回 False: "
            f"app={app_id}, err={e}",
            exc_info=True,
        )
        return False


async def refresh_active_key(conversation_id: str) -> None:
    """刷新对话活跃 key 的 TTL。

    执行 SETEX conv_active:{conversation_id} 300 1，表示对话仍在活跃状态。
    key 过期（300 秒内无新消息）即代表对话空闲，触发 Flush_Task。

    Args:
        conversation_id: 对话 ID
    """
    try:
        from app.aioRedis import get_thread_safe_redis

        redis_client = get_thread_safe_redis()
        key = f"{CONV_ACTIVE_KEY_PREFIX}{conversation_id}"
        await redis_client.set(key, "1", ex=CONV_ACTIVE_TTL_SECONDS)
        logger.debug(
            f"[MessageIngestion] 活跃 key 已刷新: key={key}, "
            f"ttl={CONV_ACTIVE_TTL_SECONDS}s"
        )
    except Exception as e:
        logger.warning(
            f"[MessageIngestion] 刷新活跃 key 失败（不影响主流程）: "
            f"conv={conversation_id}, err={e}",
            exc_info=True,
        )


async def sync_and_dispatch(
    conversation_id: str,
    app_id: str,
    original_message_id,
    role: str,
    content: str,
    created_at,
    should_memorize: bool,
    config_id: str,
    end_user_id: str,
    workspace_id: str,
    language: str,
    files: Optional[list] = None,
) -> Optional["MemoryMessage"]:
    """内部统一方法：检查门禁 → 写入 memory_messages → 刷新活跃 key → 分派调度器。

    sync_message 的核心逻辑。

    Args:
        conversation_id: 会话 ID
        app_id: 应用 ID，用于检查 memory.enabled
        original_message_id: 原始 messages 表行的 id
        role: user/assistant/system
        content: 消息内容
        created_at: 时间戳
        should_memorize: 会话级记忆开关
        config_id: 记忆配置 ID
        end_user_id: 终端用户 ID
        workspace_id: 工作空间 ID
        language: 语言
        files: 多模态文件信息列表，可为 None

    Returns:
        MemoryMessage 实例若成功写入，否则 None
    """
    # Step 0: 检查应用级记忆门禁
    if not await check_memory_enabled(app_id):
        logger.debug(
            f"[MessageIngestion] memory.enabled=false，跳过: "
            f"conv={conversation_id}, app={app_id}"
        )
        return None

    # Step 1: 写入 memory_messages 表
    memory_msg = persist_memory_message(
        conversation_id=str(conversation_id),
        original_message_id=original_message_id,
        role=role,
        content=content,
        created_at=created_at,
        should_memorize=should_memorize,
        files=files,
    )
    if memory_msg is None:
        return None

    # Step 2: 刷新 Redis 活跃 key
    await refresh_active_key(conversation_id)

    # Step 3: 分派给 SlidingWindowScheduler
    from app.core.memory.sliding_window.window_utils import dispatch_to_scheduler

    await dispatch_to_scheduler(
        conversation_id=str(conversation_id),
        config_id=config_id,
        end_user_id=end_user_id,
        workspace_id=workspace_id,
        language=language,
    )

    return memory_msg


async def sync_message(
    conversation_id: str,
    message: "Message",
    app_id: str,
    is_draft: bool = False,
    config_id: str = "",
    workspace_id: str = "",
    end_user_id: str = "",
    should_memorize: bool = True,
    language: str = "zh",
) -> Optional["MemoryMessage"]:
    """Agent 对话消息同步到 memory_messages 表。

    不依赖 memory_config，专供 conversation_service.py 调用。
    内部直接操作 memory_messages 表并分派 SlidingWindowScheduler。

    1. 检查 app_releases.config.memory.enabled（仅查发布版本，未发布的应用直接跳过）
    2. 若 false → 返回 None，消息不进入 memory_messages
    3. 若 true → 写入 memory_messages（should_memorize 由参数决定）
    4. 刷新 Redis 活跃 key
    5. 分派给 SlidingWindowScheduler

    Args:
        conversation_id: 会话 ID
        message: Message ORM 对象（已持久化到 messages 表）
        app_id: 应用 ID，用于检查 memory.enabled
        is_draft: 保留参数（当前未使用）——产品规则不区分草稿/正式
        config_id: 记忆配置 ID（传给 Scheduler，可为空）
        workspace_id: 工作空间 ID
        end_user_id: 终端用户 ID（celery_task_scheduler 的分片键，保证 per-user 串行）
        should_memorize: 会话级记忆开关——用户在前端切换的状态。
            True → 触发 Write_Pipeline 萃取；False → 仍写候选池但 cursor 只推进不萃取。
        language: 对话语言，透传给 SlidingWindowScheduler 用于下游 prompt 选择。
            调用方（conversation_service）若不知道语言可保持默认 "zh"。

    Returns:
        MemoryMessage 实例若成功写入，否则 None
    """
    return await sync_and_dispatch(
        conversation_id=conversation_id,
        app_id=app_id,
        original_message_id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        should_memorize=should_memorize,
        config_id=config_id,
        end_user_id=end_user_id,
        workspace_id=workspace_id,
        language=language,
        files=extract_files_from_message(message),
    )


async def get_conv_history(db: "Session", conv_id: str):
    return
