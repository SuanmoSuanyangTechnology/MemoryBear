"""
滑动窗口共享工具函数

scheduler.py、flush_task.py、MemoryService、MemoryAgentService 共用的：
- 窗口上下文构建（build_context_before / build_context_after）
- write_cursor 原子推进
- MemoryMessage 批量写入
- SlidingWindowScheduler 分派

数据源：所有查询均基于 memory_messages 表，仅按 conversation_id 维度过滤。
"""

from __future__ import annotations

import logging
import uuid
from bisect import bisect_right
from datetime import datetime, timezone
from typing import List

from sqlalchemy import func, select, update

from app.db import get_db_context
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

logger = logging.getLogger(__name__)

WINDOW_SIZE = 3


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
# write_cursor 原子推进
# ──────────────────────────────────────────────


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
        "created_at": (
            message.created_at.isoformat()
            if message.created_at is not None
            else None
        ),
        "dialog_at": message.dialog_at,
        "files": message.files,
    }


# ──────────────────────────────────────────────
# file_content 重建
# ──────────────────────────────────────────────


async def enrich_file_content(messages: List[dict]) -> None:
    """通过 file URL 查找已创建的 MemoryPerceptualModel，重建 file_content。

    在滑动窗口 flush 时调用此函数，弥补 write_batch_to_memory_messages
    只能序列化 files（FileInput dicts）而无法持久化 file_content（ORM 对象）的 gap。

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
# MemoryMessage 批量写入
# ──────────────────────────────────────────────

# 全局哨兵 App ID（Service API 虚拟会话专用）
SENTINEL_APP_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# 模块级缓存：哨兵 App 是否已确认存在（进程生命周期内只需检查一次）
_sentinel_app_verified: bool = False


def _ensure_sentinel_app_exists() -> None:
    """确保哨兵 App 在 apps 表中存在，不存在则自动创建。

    使用模块级 flag 缓存结果，进程生命周期内只查询一次数据库。
    其他环境首次运行时无需手动执行 migration，代码自动兜底。
    """
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
            logger.info("[WindowUtils] 创建哨兵 App: id=00000000-0000-0000-0000-000000000001")
    except Exception as e:
        # 并发场景下可能 unique violation，忽略即可；标记为已验证避免重复查询
        _sentinel_app_verified = True
        logger.debug(f"[WindowUtils] 确保哨兵 App 存在时异常（可忽略）: {e}")


def get_or_create_service_api_conversation(
    workspace_id: str,
    end_user_id: str,
) -> str:
    """按 (workspace_id, end_user_id, app_id=SENTINEL) 查找或创建虚拟会话。

    Service API（v1）写入路径专用。每个 (workspace_id, end_user_id) 对应唯一
    一条虚拟 conversation，用于承载滑动窗口的 memory_messages 和 write_cursor。

    Args:
        workspace_id: 工作空间 ID（从 API key 带入）
        end_user_id: 终端用户 ID

    Returns:
        conversation_id (str)
    """
    import uuid as _uuid

    # 确保哨兵 App 存在（首次运行时自动创建）
    _ensure_sentinel_app_exists()

    _ws_id = _uuid.UUID(workspace_id)

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
            id=_uuid.uuid4(),
            app_id=SENTINEL_APP_ID,
            workspace_id=_ws_id,
            user_id=end_user_id,
            is_draft=True,
            write_cursor=0,
        )
        db.add(conv)
        db.commit()
        logger.info(
            f"[WindowUtils] 创建 Service API 虚拟会话: "
            f"workspace_id={workspace_id}, end_user_id={end_user_id}, conv={conv.id}"
        )
        return str(conv.id)


async def ensure_conversation_exists(
    conversation_id: str,
    workspace_id: str = "",
) -> None:
    """确保 conversations 表中存在该记录，不存在时创建最小条目。

    memory_messages.conversation_id 有 FK → conversations.id 约束。
    工作流 MemoryWriteNode 等路径可能在 conversation 创建前就触发写入，
    这里做容错兜底。

    Args:
        conversation_id: 会话 ID
        workspace_id: 工作空间 ID（缺失时用 sentinel UUID）
    """
    import uuid as _uuid

    try:
        with get_db_context() as db:
            existing = db.get(Conversation, _uuid.UUID(conversation_id))
            if existing is not None:
                return

            _ws_id = _uuid.UUID(workspace_id) if workspace_id else _uuid.UUID("00000000-0000-0000-0000-000000000000")

            conv = Conversation(
                id=_uuid.UUID(conversation_id),
                app_id=SENTINEL_APP_ID,
                workspace_id=_ws_id,
                is_draft=True,
            )
            db.add(conv)
            db.commit()
            logger.info(
                f"[WindowUtils] 创建兜底 Conversation: conv={conversation_id}"
            )
    except Exception as e:
        logger.warning(
            f"[WindowUtils] ensure_conversation_exists 失败: "
            f"conv={conversation_id}, err={e}",
            exc_info=True,
        )


async def write_batch_to_memory_messages(
    conversation_id: str,
    messages: List[dict],
) -> List[MemoryMessage]:
    """批量写入 memory_messages 表，自动分配递增 message_seq。

    在单个 DB 事务中完成：查询 max(message_seq) → 逐条分配 + 写入 → commit。

    Args:
        conversation_id: 对话 ID
        messages: 消息列表，每条格式 {"role": "user"|"assistant", "content": "...", "files": [...]}

    Returns:
        成功写入的 MemoryMessage 实例列表（跳过 content 为空的消息）
    """
    written: List[MemoryMessage] = []

    with get_db_context() as db:
        max_seq_result = db.execute(
            select(func.coalesce(func.max(MemoryMessage.message_seq), 0))
            .where(MemoryMessage.conversation_id == uuid.UUID(conversation_id))
        ).scalar()
        next_seq = (max_seq_result or 0)

        for msg in messages:
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", "") or "")
            if not content.strip():
                continue

            next_seq += 1
            mm = MemoryMessage(
                id=uuid.uuid4(),
                conversation_id=uuid.UUID(conversation_id),
                original_message_id=None,
                role=role,
                content=content,
                message_seq=next_seq,
                should_memorize=True,
                created_at=datetime.now(timezone.utc),
                dialog_at=(msg.get("dialog_at") or None),
                files=msg.get("files"),
            )
            db.add(mm)
            written.append(mm)
            logger.debug(
                f"[WindowUtils] 写入 memory_messages: "
                f"conv={conversation_id}, seq={next_seq}, role={role}"
            )

        db.commit()

    # 标记对话有待处理消息，供 scan_idle 快速过滤
    if written:
        mark_conversation_pending(conversation_id)

    return written


# ──────────────────────────────────────────────
# SlidingWindowScheduler 分派
# ──────────────────────────────────────────────

# Redis Set key：存储有待处理消息的对话 ID 集合
# scan_idle_conversations_task 优先从此 Set 读取候选，避免全表 JOIN 扫描
# 写在 settings.REDIS_DB（与 conv_active 同一 DB）
PENDING_CONVERSATIONS_SET_KEY = "pending_conversations"


def mark_conversation_pending(conversation_id: str) -> None:
    """将对话 ID 加入 pending_conversations Redis Set。

    在 write_batch_to_memory_messages 成功后调用，供 scan_idle_conversations_task
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


def unmark_conversation_pending(conversation_id: str) -> None:
    """将对话 ID 从 pending_conversations Redis Set 中移除。

    在 execute_pending_from_pool 处理完所有消息（pending 为空）后调用。

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
) -> int:
    """Layer 2：从 memory_messages 池中拉取并执行滑动窗口写入。

    流程：
    1. 加载 memory_config
    2. 查询 write_cursor
    3. 查询 message_seq > write_cursor 的所有消息
    4. 顺序处理：
       - should_memorize=FALSE → 原子推进 write_cursor
       - role=user + should_memorize=TRUE：
         · 若 enforce_window=True（默认，实时滑动窗口路径）：检查下游
           memorable user Q 是否 ≥ WINDOW_SIZE。不够就停止处理，保留
           给后续触发；这样 design.md 的"等待下文凑齐 3 条"语义生效。
         · 若 enforce_window=False（FlushTask / API 同步写入路径）：
           无视下文条件，强制处理。
       - role=assistant → 跳过（WritePipeline 内部 Pruned_Context 阶段处理）

    Args:
        conversation_id: 对话 ID
        end_user_id: 终端用户 ID
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        language: 语言
        enforce_window: 是否要求下文 ≥ WINDOW_SIZE 才处理 user 消息。
            实时滑动窗口路径传 True；兜底场景（FlushTask、API 同步）传 False。

    Returns:
        处理的消息数（含 should_memorize=FALSE 跳过的）
    """
    from app.core.memory.pipelines.write_pipeline import WritePipeline
    from app.services.memory_config_service import MemoryConfigService
    import asyncio as _asyncio
    import uuid as _uuid

    # 重试配置
    MAX_RETRIES = 3
    _retry_delays = [1, 2, 4]  # 指数退避：1s, 2s, 4s

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
        # cursor 已追上 max_seq，主动清理 Redis Set 残留，避免后续 ScanIdle 反复派发空 flush
        unmark_conversation_pending(conversation_id)
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

    # 仅在需要校验下文长度时（实时滑动窗口路径）一次性加载 user seq
    # 列表，循环里用 bisect_right 替代 N 次 COUNT 查询，开销从 N 次 SQL
    # 降到 1 次 SQL + O(N log N) 内存查找。
    # 注意：列表包含 should_memorize=false 的 user 消息——它们作为窗口
    # 上下文计入下文长度，但不会触发写入（在主循环里 advance_write_cursor
    # 跳过）。
    memorable_user_seqs: List[int] = []
    if enforce_window:
        memorable_user_seqs = await _load_user_seqs(conversation_id)

    for message in pending:
        target_seq = message.get("message_seq")
        if target_seq is None:
            continue

        try:
            if not message.get("should_memorize", True):
                await advance_write_cursor(conversation_id, target_seq)
                processed += 1
                continue

            if message.get("role") != "user":
                continue

            # 实时滑动窗口路径：检查下文 ≥ WINDOW_SIZE 的 memorable user Q，
            # 不够就停止处理（保留给以后触发）。这样保证萃取上下文充分。
            if enforce_window:
                downstream_count = (
                    len(memorable_user_seqs)
                    - bisect_right(memorable_user_seqs, target_seq)
                )
                if downstream_count < WINDOW_SIZE:
                    logger.info(
                        f"[execute_pending_from_pool] 下文不足 ({downstream_count} < {WINDOW_SIZE})"
                        f"，停止处理: conv={conversation_id}, seq={target_seq}"
                    )
                    break

            # 上下文构建（不在重试范围内——DB 查询失败直接抛出）
            context_before = await build_context_before(conversation_id, target_seq)
            context_after = await build_context_after(conversation_id, target_seq)

            # 带重试的写入操作
            for attempt in range(MAX_RETRIES):
                try:
                    await write_pipeline.run_with_window(
                        target_message=message,
                        context_before=context_before,
                        context_after=context_after,
                        conversation_id=conversation_id,
                        message_seq=target_seq,
                    )
                    processed += 1
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        delay = _retry_delays[attempt]
                        logger.warning(
                            f"[execute_pending_from_pool] 写入失败，{delay}s 后重试 "
                            f"({attempt + 1}/{MAX_RETRIES}): "
                            f"conv={conversation_id}, seq={target_seq}, err={e}"
                        )
                        await _asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"[execute_pending_from_pool] 写入失败，已重试 {MAX_RETRIES} 次，"
                            f"中断本轮写入: conv={conversation_id}, seq={target_seq}, err={e}",
                            exc_info=True,
                        )
                        raise

        except Exception as e:
            # 非重试覆盖的异常（如 advance_write_cursor 失败、上下文构建失败）直接中断
            logger.error(
                f"[execute_pending_from_pool] 处理失败，中断本轮写入: "
                f"conv={conversation_id}, seq={target_seq}, err={e}",
                exc_info=True,
            )
            raise

    logger.info(
        f"[execute_pending_from_pool] 完成: conv={conversation_id}, processed={processed}"
    )

    # 清理 Redis Set 时机：本轮所有"应处理"的消息（user 或 should_memorize=false）都已处理完
    # processed/expected 同为 0 时也应清理（说明 pending 中只剩 assistant 消息或本轮无可处理）
    expected = sum(
        1 for m in pending
        if m.get("role") == "user" or not m.get("should_memorize", True)
    )
    if processed >= expected:
        unmark_conversation_pending(conversation_id)

    return processed
