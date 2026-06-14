"""
滑动窗口共享工具函数

scheduler.py、flush_task.py、MemoryService 共用的：
- 窗口上下文构建（build_context_before / build_context_after）
- 内存上下文构建（不查询 DB，供直接写入路径使用）
- write_cursor 原子推进
- SlidingWindowScheduler 分派
- Redis pending 集合管理

数据源：Agent 对话路径基于 memory_messages 表，直接写入路径基于内存 messages 数组。
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from datetime import datetime
from typing import List

from sqlalchemy import func, select, update

from app.db import get_db_context
from app.core.utils.datetime_utils import to_iso_z
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

logger = logging.getLogger(__name__)

WINDOW_SIZE = 3

# Redis ZSET key 前缀，记录已写入 Neo4j 的 message_seq（按时间戳 score 排序）
# 仅用于故障排查，不参与业务逻辑
WRITTEN_SEQS_ZSET_KEY_PREFIX = "written_seqs:conv:"
# ZSET 保留天数
WRITTEN_SEQS_ZSET_TTL_SECONDS = 86400 * 7
# ZSET 最大成员数（超出后自动 trim 旧数据）
WRITTEN_SEQS_ZSET_MAX_SIZE = 1000


# ──────────────────────────────────────────────
# 窗口上下文构建
# ──────────────────────────────────────────────


async def build_context_before(
    conversation_id: str,
    target_seq: int,
) -> List[dict]:
    """构建上文消息列表（从 memory_messages 表）。

    向前查找最多 WINDOW_SIZE 个 user 消息，取最小 message_seq 作为上边界，
    查询 [upper_bound, target_seq) 范围内所有消息（含穿插的 A），
    按 message_seq 升序排列。
    """
    try:
        with get_db_context() as db:
            upstream_q_seqs = (
                db.execute(
                    select(MemoryMessage.message_seq)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.role == "user",
                        MemoryMessage.message_seq < target_seq,
                    )
                    .order_by(MemoryMessage.message_seq.desc())
                    .limit(WINDOW_SIZE)
                )
                .scalars()
                .all()
            )

            if not upstream_q_seqs:
                return []

            upper_bound = min(upstream_q_seqs)

            messages = (
                db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq >= upper_bound,
                        MemoryMessage.message_seq < target_seq,
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                )
                .scalars()
                .all()
            )

            return [message_to_dict(msg) for msg in messages]
    except Exception as e:
        logger.error(
            f"[WindowUtils] 构建上文失败: "
            f"conv={conversation_id}, target_seq={target_seq}, err={e}",
            exc_info=True,
        )
        return []


async def build_context_after(
    conversation_id: str,
    target_seq: int,
) -> List[dict]:
    """构建下文消息列表（从 memory_messages 表）。

    向后查找最多 WINDOW_SIZE 个 user 消息，取最大 message_seq 作为下边界，
    查询 (target_seq, lower_bound] 范围内所有消息（含穿插的 A），
    按 message_seq 升序排列。
    """
    try:
        with get_db_context() as db:
            downstream_q_seqs = (
                db.execute(
                    select(MemoryMessage.message_seq)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.role == "user",
                        MemoryMessage.message_seq > target_seq,
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                    .limit(WINDOW_SIZE)
                )
                .scalars()
                .all()
            )

            if not downstream_q_seqs:
                return []

            lower_bound = max(downstream_q_seqs)

            messages = (
                db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq > target_seq,
                        MemoryMessage.message_seq <= lower_bound,
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                )
                .scalars()
                .all()
            )

            return [message_to_dict(msg) for msg in messages]
    except Exception as e:
        logger.error(
            f"[WindowUtils] 构建下文失败: "
            f"conv={conversation_id}, target_seq={target_seq}, err={e}",
            exc_info=True,
        )
        return []


# ──────────────────────────────────────────────
# 内存上下文构建（不查询 DB）
# 用于 workflow / API Service 直接写入路径：
# messages 已在内存中，直接从数组构建上下文，无需回查 memory_messages 表。
# ──────────────────────────────────────────────


def build_context_before_in_memory(
    messages: List[dict],
    target_index: int,
) -> List[dict]:
    """从内存 messages 数组构建上文。

    向前查找最多 WINDOW_SIZE 个 user 消息作为上下文，
    取最小 index 作为上边界，返回 [upper_bound, target_index) 范围内所有消息。

    Args:
        messages: 完整消息列表 [{"role": ..., "content": ...}, ...]
        target_index: 当前目标消息在 messages 数组中的索引

    Returns:
        上文消息列表（保持原顺序）
    """
    result: List[dict] = []
    user_indices: List[int] = []

    # 收集 target 之前所有 user 消息的 index（最多 WINDOW_SIZE 个）
    for i in range(target_index - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_indices.append(i)
            if len(user_indices) >= WINDOW_SIZE:
                break

    if not user_indices:
        return []

    upper_bound = min(user_indices)
    for i in range(upper_bound, target_index):
        result.append(messages[i])

    return result


def build_context_after_in_memory(
    messages: List[dict],
    target_index: int,
) -> List[dict]:
    """从内存 messages 数组构建下文。

    向后查找最多 WINDOW_SIZE 个 user 消息作为上下文，
    取最大 index 作为下边界，返回 (target_index, lower_bound] 范围内所有消息。

    Args:
        messages: 完整消息列表
        target_index: 当前目标消息在 messages 数组中的索引

    Returns:
        下文消息列表（保持原顺序）
    """
    result: List[dict] = []
    user_indices: List[int] = []

    n = len(messages)
    for i in range(target_index + 1, n):
        if messages[i].get("role") == "user":
            user_indices.append(i)
            if len(user_indices) >= WINDOW_SIZE:
                break

    if not user_indices:
        return []

    lower_bound = max(user_indices)
    for i in range(target_index + 1, lower_bound + 1):
        result.append(messages[i])

    return result


# ──────────────────────────────────────────────
# 预计算上下文窗口（O(n) 单次遍历）
# 用于 write_messages_direct 批量写入：一次扫描所有 user 索引，
# 为每条 user 消息预计算 context_before / context_after 的切片范围。
# ──────────────────────────────────────────────

from dataclasses import dataclass


@dataclass
class _ContextWindow:
    """单条 user 消息的上下文窗口切片范围。"""
    index: int          # 目标消息在 messages 中的索引
    before_start: int   # context_before 起始索引（含）
    before_end: int     # context_before 结束索引（不含）= target_index
    after_start: int    # context_after 起始索引（含）= target_index + 1
    after_end: int      # context_after 结束索引（不含）


def precompute_context_windows(messages: List[dict]) -> List[_ContextWindow]:
    """预计算所有 user 消息的上下文窗口切片范围。

    单次遍历 messages 收集 user 索引，再为每条 user 消息计算窗口边界。
    时间复杂度 O(n)，替代每条消息单独调用 build_context_*_in_memory（O(n²)）。

    Returns:
        ContextWindow 列表，按 message 在数组中的顺序排列。
    """
    user_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "user"
    ]
    if not user_indices:
        return []

    windows: List[_ContextWindow] = []

    for p, idx in enumerate(user_indices):
        # 上文：向前找最多 WINDOW_SIZE 个 user 消息
        before_user_p = max(0, p - WINDOW_SIZE)
        before_start = user_indices[before_user_p]
        before_end = idx

        # 下文：向后找最多 WINDOW_SIZE 个 user 消息
        after_user_p = min(len(user_indices) - 1, p + WINDOW_SIZE)
        after_start = idx + 1
        after_end = user_indices[after_user_p] + 1  # +1 使得切片包含边界

        windows.append(_ContextWindow(
            index=idx,
            before_start=before_start,
            before_end=before_end,
            after_start=after_start,
            after_end=after_end,
        ))

    return windows


# ──────────────────────────────────────────────
# write_cursor 原子推进
# ──────────────────────────────────────────────


def _record_written_seq_to_redis(conversation_id: str, message_seq: int) -> None:
    """记录已写入的 message_seq 到 Redis ZSET，仅用于故障排查。

    key: written_seqs:conv:{conversation_id}
    member: message_seq（string）
    score: 当前时间戳（精确到秒）

    自动设置 TTL 并 trim 超出 WRITTEN_SEQS_ZSET_MAX_SIZE 的旧数据。
    fire-and-forget：失败仅记录 debug 日志。
    """
    try:
        import time
        import redis as _redis
        from app.core.config import settings

        r = _redis.StrictRedis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        key = f"{WRITTEN_SEQS_ZSET_KEY_PREFIX}{conversation_id}"
        now = int(time.time())
        pipe = r.pipeline()
        pipe.zadd(key, {str(message_seq): now})
        pipe.zremrangebyrank(key, 0, -(WRITTEN_SEQS_ZSET_MAX_SIZE + 1))
        pipe.expire(key, WRITTEN_SEQS_ZSET_TTL_SECONDS)
        pipe.execute()
        pipe.reset()
        r.close()
    except Exception as e:
        logger.debug(f"[WindowUtils] _record_written_seq 失败（可忽略）: {e}")


async def advance_write_cursor(
    conversation_id: str,
    message_seq: int,
) -> None:
    """原子推进 write_cursor。

    UPDATE conversations SET write_cursor = :seq
    WHERE id = :conv_id AND write_cursor < :seq，
    确保 write_cursor 只能单调递增。
    """
    try:
        with get_db_context() as db:
            db.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.write_cursor < message_seq,
                )
                .values(write_cursor=message_seq)
            )
            db.commit()
            logger.debug(
                f"[WindowUtils] write_cursor 已推进: conv={conversation_id}, seq={message_seq}"
            )
            # 记录已写入 seq 到 Redis ZSET（故障排查用）
            _record_written_seq_to_redis(conversation_id, message_seq)
    except Exception as e:
        logger.warning(
            f"[WindowUtils] 推进 write_cursor 失败: "
            f"conv={conversation_id}, seq={message_seq}, err={e}",
            exc_info=True,
        )


# ──────────────────────────────────────────────
# 辅助转换
# ──────────────────────────────────────────────


def message_to_dict(message: MemoryMessage) -> dict:
    """将 MemoryMessage ORM 对象转换为字典格式。"""
    return {
        "role": message.role,
        "content": message.content,
        "message_seq": message.message_seq,
        "should_memorize": message.should_memorize,
        "created_at": to_iso_z(message.created_at),
        "dialog_at": message.dialog_at,
        "files": message.files,
    }


# ──────────────────────────────────────────────
# file_content 重建
# ──────────────────────────────────────────────


async def enrich_file_content(messages: List[dict]) -> None:
    """通过 file URL 查找已创建的 MemoryPerceptualModel，重建 file_content。

    在滑动窗口 flush 时调用此函数，弥补 message_to_dict 只能序列化
    files（FileInput dicts）而无法持久化 file_content（ORM 对象）的 gap。

    Args:
        messages: 消息列表，每元素含 files 字段（List[FileInput dict]）。
                  函数会原地修改，为有 files 的消息注入 file_content。
    """
    if not messages:
        return

    from app.repositories.memory_perceptual_repository import MemoryPerceptualRepository

    for msg in messages:
        files = msg.get("files") or []
        if not files:
            continue
        file_content = []
        try:
            with get_db_context() as db:
                repo = MemoryPerceptualRepository(db)
                for file_info in files:
                    url = file_info.get("url", "")
                    if not url:
                        continue
                    memories = repo.get_by_url(url)
                    if not memories:
                        continue
                    # 同一 URL 可能因多次 API 调用而存在多条记录，
                    # 只取最新的一条（按 created_time 降序），避免重复 Perceptual 节点
                    memory = max(
                        memories,
                        key=lambda m: m.created_time if m.created_time else datetime.min,
                    )
                    # 在 Session 关闭前显式访问所有需要的属性，
                    # 确保它们被加载到内存中，避免 detach 后
                    # 访问 expired 属性触发 DetachedInstanceError
                    _ = memory.meta_data
                    _ = memory.summary
                    _ = memory.file_path
                    _ = memory.file_name
                    _ = memory.file_ext
                    _ = memory.perceptual_type
                    _ = memory.end_user_id
                    _ = memory.id
                    _ = memory.created_time
                    db.expunge(memory)
                    file_content.append((memory, file_info.get("type", "")))
        except Exception as e:
            logger.warning(
                f"[WindowUtils] 重建 file_content 失败: err={e}"
            )
        msg["file_content"] = file_content


# ──────────────────────────────────────────────
# SlidingWindowScheduler 分派
# ──────────────────────────────────────────────

# Redis Set key：存储有待处理消息的对话 ID 集合
# flush_conversation_task 扫描模式优先从此 Set 读取候选，避免全表 JOIN 扫描
# 写在 settings.REDIS_DB（与 conv_active 同一 DB）
PENDING_CONVERSATIONS_SET_KEY = "pending_conversations"


def mark_conversation_pending(conversation_id: str) -> None:
    """将对话 ID 加入 pending_conversations Redis Set。

    在消息写入 memory_messages 表后调用，供 flush_conversation_task 扫描模式
    作为候选集快速过滤，避免每次 Beat 都做全表 JOIN。

    fire-and-forget：失败仅记录 debug 日志。
    """
    try:
        import redis as _redis
        from app.core.config import settings

        r = _redis.StrictRedis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        r.sadd(PENDING_CONVERSATIONS_SET_KEY, conversation_id)
        r.close()
    except Exception as e:
        logger.debug(f"[WindowUtils] mark_conversation_pending 失败（可忽略）: {e}")


def verify_unmark_safe(conversation_id: str) -> bool:
    """在 unmark 前验证对话确实没有待写入消息。

    重新查询 DB，比较 max(message_seq) 与 write_cursor，
    防止并发投递的新消息在 unmark 后丢失。

    Returns:
        True 表示可以安全 unmark（write_cursor >= max_seq）。
        False 表示仍有待写入消息，不能 unmark。
    """
    try:
        with get_db_context() as db:
            max_seq = db.execute(
                select(func.max(MemoryMessage.message_seq)).where(
                    MemoryMessage.conversation_id == conversation_id
                )
            ).scalar()

            cursor = db.execute(
                select(Conversation.write_cursor).where(
                    Conversation.id == conversation_id
                )
            ).scalar_one_or_none() or 0

            # 无消息 或 cursor 已覆盖所有消息 → 安全
            return max_seq is None or max_seq <= cursor
    except Exception as e:
        logger.warning(
            f"[WindowUtils] verify_unmark_safe 失败，保守返回 False: "
            f"conv={conversation_id}, err={e}"
        )
        return False


def unmark_conversation_pending(conversation_id: str) -> None:
    """将对话 ID 从 pending_conversations Redis Set 中移除。

    调用方应先在 DB 中验证 write_cursor >= max(message_seq)。
    仅作为最终一致性手段清理 Set 残留，失败不报错。

    fire-and-forget：失败仅记录 debug 日志。
    """
    try:
        import redis as _redis
        from app.core.config import settings

        r = _redis.StrictRedis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        r.srem(PENDING_CONVERSATIONS_SET_KEY, conversation_id)
        r.close()
    except Exception as e:
        logger.debug(f"[WindowUtils] unmark_conversation_pending 失败（可忽略）: {e}")


async def dispatch_to_scheduler(
    conversation_id: str,
    config_id: str = "",
    end_user_id: str = "",
    workspace_id: str = "",
    language: str = "zh",
) -> None:
    """分派 SlidingWindowScheduler（Agent 对话路径，fire-and-forget）。

    失败只记 warning 日志，不抛异常。
    """
    try:
        from app.core.memory.sliding_window.scheduler import SlidingWindowScheduler

        scheduler = SlidingWindowScheduler()
        await scheduler.check_and_dispatch(
            conversation_id=conversation_id,
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            language=language,
        )
    except Exception as e:
        logger.warning(
            f"[WindowUtils] 分派 SlidingWindowScheduler 失败（不影响主流程）: "
            f"conv={conversation_id}, err={e}",
            exc_info=True,
        )


# ──────────────────────────────────────────────
# Layer 2: 从候选池执行滑动窗口写入
# ──────────────────────────────────────────────


async def _load_user_seqs(conversation_id: str) -> List[int]:
    """一次性拉取 conversation 中所有 role=user 的 message_seq 升序列表。

    用于在 execute_pending_from_pool 顺序处理多条 user 消息时校验下文长度：
    给定该列表后，下文条数 = len(seqs) - bisect_right(seqs, target_seq)，
    避免对每条 target_seq 各跑一次 COUNT 查询。

    注意：should_memorize=false 的 user 消息也计入下文，仅作为窗口上下文凑数；
    这些消息的 Neo4j 写入会在 execute_pending_from_pool 主循环里被 advance_write_cursor
    跳过推进。

    Args:
        conversation_id: 对话 ID

    Returns:
        升序排列的 message_seq 列表；查询失败时返回空列表
    """
    try:
        with get_db_context() as db:
            rows = db.execute(
                select(MemoryMessage.message_seq).where(
                    MemoryMessage.conversation_id == conversation_id,
                    MemoryMessage.role == "user",
                ).order_by(MemoryMessage.message_seq.asc())
            ).scalars().all()
            return [int(s) for s in rows if s is not None]
    except Exception as e:
        logger.error(
            f"[WindowUtils] 加载 user seq 列表失败: "
            f"conv={conversation_id}, err={e}",
            exc_info=True,
        )
        return []


async def execute_pending_from_pool(
    conversation_id: str,
    end_user_id: str,
    config_id: str = "",
    workspace_id: str = "",
    language: str = "zh",
    enforce_window: bool = True,
    target_seq: int | None = None,
) -> int:
    """Layer 2：从 memory_messages 池中拉取并执行滑动窗口写入。

    两种执行模式：

    **target_seq 模式**（实时路径，target_seq 非空）：
      直接按 message_seq = target_seq 查询目标消息，不依赖 write_cursor 扫 pending。
      push_task 时已提前推进 cursor，重投递仍可通过 target_seq 找到消息正确执行。

    **扫描模式**（兜底路径，target_seq 为空）：
      查询 message_seq > write_cursor 的所有 pending 消息，顺序处理。

    流程（扫描模式）：
    1. 加载 memory_config
    2. 查询 write_cursor
    3. 查询 message_seq > write_cursor 的所有消息 # 现在需要修改这个逻辑，直接查询write_seq查询需要写入的message
    4. 顺序处理：
       - should_memorize=FALSE → 原子推进 write_cursor
       - role=user + should_memorize=TRUE：
         · 若 enforce_window=True（默认，实时滑动窗口路径）：检查下游
           memorable user Q 是否 ≥ WINDOW_SIZE。不够就停止处理，保留
           给后续触发；这样 design.md 的"等待下文凑齐 3 条"语义生效。
         · 若 enforce_window=False（FlushTask / API 同步写入路径）：
           无视下文条件，强制处理。
       - role=assistant + should_memorize=TRUE → 调用 PruningPipeline.prune() 后推进 cursor
       - role=assistant + should_memorize=FALSE → 推进 cursor

    Args:
        conversation_id: 对话 ID
        end_user_id: 终端用户 ID
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        language: 语言
        enforce_window: 是否要求下文 ≥ WINDOW_SIZE 才处理 user 消息。
            实时滑动窗口路径传 True；兜底场景（FlushTask、API 同步）传 False。
        target_seq: 目标消息 seq（实时路径携带）。非空时走 target_seq 直查模式；
            cursor 已提前推进，重投递时仍可通过此字段找到消息。

    Returns:
        处理的消息数（含 should_memorize=FALSE 跳过的）
    """
    from app.core.memory.pipelines.write_pipeline import WritePipeline
    from app.services.memory_config_service import MemoryConfigService
    import uuid as _uuid

    if not conversation_id:
        logger.warning("[execute_pending_from_pool] conversation_id 为空，跳过")
        return 0

    # 1. 在单个 DB 会话中完成：反查 workspace_id + 加载 config + 查 write_cursor + 查 pending 消息
    # 减少连接开销（原来 4 次 get_db_context 合并为 1 次）
    try:
        if not workspace_id:
            with get_db_context() as db:
                row = db.execute(
                    select(Conversation.workspace_id).where(
                        Conversation.id == conversation_id
                    )
                ).scalar_one_or_none()
                if row:
                    workspace_id = str(row)
                    logger.info(
                        f"[execute_pending_from_pool] 从 conversation 反查 workspace_id: "
                        f"conv={conversation_id}, workspace_id={workspace_id}"
                    )
    except Exception as e:
        logger.warning(
            f"[execute_pending_from_pool] 反查 workspace_id 失败: "
            f"conv={conversation_id}, err={e}"
        )

    try:
        try:
            _workspace_id = _uuid.UUID(workspace_id) if workspace_id else None
        except (ValueError, AttributeError):
            logger.warning(
                f"[execute_pending_from_pool] workspace_id 非合法 UUID，回退为 None: "
                f"conv={conversation_id}, workspace_id={workspace_id!r}"
            )
            _workspace_id = None
        try:
            _config_id = _uuid.UUID(config_id) if config_id else None
        except (ValueError, AttributeError):
            logger.warning(
                f"[execute_pending_from_pool] config_id 非合法 UUID，回退为 None: "
                f"conv={conversation_id}, config_id={config_id!r}"
            )
            _config_id = None

        # 合并查询：在同一个 session 中加载 config、查 write_cursor、查 pending 消息
        with get_db_context() as db:
            memory_config = MemoryConfigService(db).load_memory_config(
                config_id=_config_id,
                workspace_id=_workspace_id,
                service_name="execute_pending_from_pool",
            )

            write_cursor = db.execute(
                select(Conversation.write_cursor).where(
                    Conversation.id == conversation_id
                )
            ).scalar_one_or_none()

            if write_cursor is None:
                write_cursor = 0

            # target_seq 模式：直接按 seq 查询，不依赖 write_cursor 扫 pending
            # cursor 已提前推进，但通过 target_seq 仍可找到目标消息（重投递安全）
            if target_seq is not None:
                target_orm = db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq == target_seq,
                    )
                ).scalar_one_or_none()

                if target_orm is None:
                    logger.warning(
                        f"[execute_pending_from_pool] target_seq 消息不存在（可能已被删除）: "
                        f"conv={conversation_id}, target_seq={target_seq}"
                    )
                    return 0

                pending = [message_to_dict(target_orm)]
            else:
                # 扫描模式：按 write_cursor 查 pending
                pending_orm = (
                    db.execute(
                        select(MemoryMessage)
                        .where(
                            MemoryMessage.conversation_id == conversation_id,
                            MemoryMessage.message_seq > write_cursor,
                        )
                        .order_by(MemoryMessage.message_seq.asc())
                    )
                    .scalars()
                    .all()
                )
                pending = [message_to_dict(m) for m in pending_orm]

    except Exception as e:
        logger.error(
            f"[execute_pending_from_pool] 加载配置/查询数据失败: "
            f"conv={conversation_id}, config_id={config_id}, "
            f"workspace_id={workspace_id}, err={e}",
            exc_info=True,
        )
        return 0

    if not pending:
        logger.debug(
            f"[execute_pending_from_pool] 无待处理消息: "
            f"conv={conversation_id}, write_cursor={write_cursor}"
        )
        # 二次确认：防止并发写入的新消息在 pending 查询后到达
        if verify_unmark_safe(conversation_id):
            unmark_conversation_pending(conversation_id)
        else:
            logger.info(
                f"[execute_pending_from_pool] 并发新消息到达，保留 Set: "
                f"conv={conversation_id}"
            )
        return 0

    logger.info(
        f"[execute_pending_from_pool] 待处理消息: {len(pending)}, "
        f"conv={conversation_id}, write_cursor={write_cursor}"
    )

    processed = 0
    write_pipeline = WritePipeline(
        memory_config=memory_config,
        end_user_id=end_user_id,
        language=language,
    )

    # 初始化 PruningPipeline（用于处理 assistant 消息）
    from app.core.memory.pipelines.pruning_pipeline import PruningPipeline
    pruning_pipeline = PruningPipeline(
        memory_config=memory_config,
        end_user_id=end_user_id,
        language=language,
    )

    # 仅在需要校验下文长度时（实时滑动窗口路径 + 扫描模式）一次性加载 user seq 列表
    # target_seq 模式下跳过（scheduler 已确认下文满足条件）
    memorable_user_seqs: List[int] = []
    if enforce_window and target_seq is None:
        memorable_user_seqs = await _load_user_seqs(conversation_id)

    for message in pending:
        msg_seq = message.get("message_seq")
        if msg_seq is None:
            continue

        try:
            if not message.get("should_memorize", True):
                await advance_write_cursor(conversation_id, msg_seq)
                processed += 1
                continue

            # assistant 消息处理：调用 PruningPipeline.prune() 后推进 cursor
            if message.get("role") == "assistant":
                await pruning_pipeline.prune(
                    conversation_id=conversation_id,
                    message_seq=msg_seq,
                    content=message.get("content") or "",
                )
                await advance_write_cursor(conversation_id, msg_seq)
                processed += 1
                logger.info(
                    f"[execute_pending_from_pool] assistant 消息剪枝完成: "
                    f"conv={conversation_id}, seq={msg_seq}"
                )
                continue

            if message.get("role") != "user":
                # 其他角色（system 等）直接推进 cursor
                await advance_write_cursor(conversation_id, msg_seq)
                processed += 1
                continue

            # 实时滑动窗口路径（扫描模式，target_seq 参数为空）：
            # 检查下文 ≥ WINDOW_SIZE 的 memorable user Q，
            # 不够就停止处理（保留给以后触发）。
            # target_seq 模式（target_seq 参数非空）跳过此检查：
            # scheduler 已在派发时确认下文满足条件。
            if enforce_window and target_seq is None:
                downstream_count = (
                    len(memorable_user_seqs)
                    - bisect_right(memorable_user_seqs, msg_seq)
                )
                if downstream_count < WINDOW_SIZE:
                    logger.info(
                        f"[execute_pending_from_pool] 下文不足 ({downstream_count} < {WINDOW_SIZE})"
                        f"，停止处理: conv={conversation_id}, seq={msg_seq}"
                    )
                    break

            context_before = await build_context_before(conversation_id, msg_seq)
            context_after = await build_context_after(conversation_id, msg_seq)

            await write_pipeline.run_with_window(
                target_message=message,
                context_before=context_before,
                context_after=context_after,
                conversation_id=conversation_id,
                message_seq=msg_seq,
            )
            processed += 1

        except Exception as e:
            logger.error(
                f"[execute_pending_from_pool] 消息处理异常，跳过: "
                f"conv={conversation_id}, seq={msg_seq}, err={e}",
                exc_info=True,
            )
            continue

    logger.info(
        f"[execute_pending_from_pool] 完成: conv={conversation_id}, processed={processed}"
    )

    # 清理 Redis Set：仍需二次验证 DB，因为处理期间可能有新消息并发写入
    # 原逻辑仅基于"本轮 pending 快照"判断 → 并发新消息的 mark 会被 unmark 冲掉
    # 现在改为直接查询 DB max(message_seq) vs write_cursor，消除竞态
    if verify_unmark_safe(conversation_id):
        unmark_conversation_pending(conversation_id)
    else:
        logger.info(
            f"[execute_pending_from_pool] 处理期间有新消息到达，保留 Set: "
            f"conv={conversation_id}"
        )

    return processed
