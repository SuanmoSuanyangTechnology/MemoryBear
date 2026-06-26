"""
MemoryMessagePoolExecutor — 从 memory_messages 池消费并执行写入

[DEPRECATED] 下一版本移除：供 /writer_service 同步路径使用，
将改为统一走 dispatcher → push_task → write_message_task 异步路径。

从 memory_messages 表中按 write_cursor 拉取待处理消息，
逐条调用 WritePipeline.run() 完成 Neo4j 写入。
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from typing import List

from sqlalchemy import select

from app.db import get_db_context
from app.models.conversation_model import Conversation

logger = logging.getLogger(__name__)

WINDOW_SIZE = 3


async def execute_pending_from_pool(
    conversation_id: str,
    end_user_id: str,
    config_id: str = "",
    workspace_id: str = "",
    language: str = "zh",
    enforce_window: bool = False,
) -> int:
    """从 memory_messages 池中拉取待处理消息并逐条执行写入。

    供 /writer_service 同步路径使用：写入 memory_messages 表后，
    在当前请求内直接消费所有 pending 消息。

    Args:
        conversation_id: 对话 ID
        end_user_id: 终端用户 ID
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        language: 语言
        enforce_window: 是否要求下文 ≥ WINDOW_SIZE 才处理 user 消息（默认 False）

    Returns:
        处理的消息数
    """
    from app.core.memory.pipelines.write_pipeline import WritePipeline
    from app.core.memory.memory_service import MemoryService as _MS
    from app.repositories.memory_message_repository import MemoryMessageRepository, message_to_dict
    from app.services.memory_config_service import MemoryConfigService
    import uuid as _uuid

    if not conversation_id:
        logger.warning("[execute_pending_from_pool] conversation_id 为空，跳过")
        return 0

    # 反查 workspace_id
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
    except Exception as e:
        logger.warning(f"[execute_pending_from_pool] 反查 workspace_id 失败: conv={conversation_id}, err={e}")

    # 解析 UUID 参数
    _workspace_id = None
    _config_id = None
    try:
        _workspace_id = _uuid.UUID(workspace_id) if workspace_id else None
    except (ValueError, AttributeError):
        pass
    try:
        _config_id = _uuid.UUID(config_id) if config_id else None
    except (ValueError, AttributeError):
        pass

    # 加载 memory_config（独立 Session，查完立即释放）
    try:
        with get_db_context() as db:
            memory_config = MemoryConfigService(db).load_memory_config(
                config_id=_config_id,
                workspace_id=_workspace_id,
                service_name="execute_pending_from_pool",
            )
    except Exception as e:
        logger.error(
            f"[execute_pending_from_pool] 加载配置失败: conv={conversation_id}, err={e}",
            exc_info=True,
        )
        return 0

    # 查询 pending 消息（独立 Session，查完立即释放）
    try:
        with get_db_context() as db:
            repo = MemoryMessageRepository(db)
            write_cursor = repo.get_write_cursor(conversation_id) or 0
            pending_orm = repo.get_pending_messages(conversation_id, write_cursor)
            pending = [message_to_dict(m) for m in pending_orm]
    except Exception as e:
        logger.error(
            f"[execute_pending_from_pool] 查询待处理消息失败: conv={conversation_id}, err={e}",
            exc_info=True,
        )
        return 0

    if not pending:
        logger.debug(f"[execute_pending_from_pool] 无待处理消息: conv={conversation_id}")
        if _MS.verify_unmark_safe(conversation_id):
            _MS.unmark_conversation_pending(conversation_id)
        return 0

    logger.info(f"[execute_pending_from_pool] 待处理消息: {len(pending)}, conv={conversation_id}")

    processed = 0
    write_pipeline = WritePipeline(
        memory_config=memory_config,
        end_user_id=end_user_id,
        language=language,
    )

    from app.core.memory.pipelines.pruning_pipeline import PruningPipeline
    pruning_pipeline = PruningPipeline(
        memory_config=memory_config,
        end_user_id=end_user_id,
        language=language,
    )

    # 仅在 enforce_window=True 时加载 user seq 列表（用于下文条数检查）
    memorable_user_seqs: List[int] = []
    if enforce_window:
        with get_db_context() as db:
            repo = MemoryMessageRepository(db)
            memorable_user_seqs = repo.get_user_seqs(conversation_id)

    for message in pending:
        msg_seq = message.get("message_seq")
        if msg_seq is None:
            continue

        try:
            if not message.get("should_memorize", True):
                with get_db_context() as db:
                    MemoryMessageRepository(db).advance_write_cursor(conversation_id, msg_seq)
                    db.commit()
                processed += 1
                continue

            if message.get("role") == "assistant":
                await pruning_pipeline.prune(
                    conversation_id=conversation_id,
                    message_seq=msg_seq,
                    content=message.get("content") or "",
                )
                with get_db_context() as db:
                    MemoryMessageRepository(db).advance_write_cursor(conversation_id, msg_seq)
                    db.commit()
                processed += 1
                continue

            if message.get("role") != "user":
                with get_db_context() as db:
                    MemoryMessageRepository(db).advance_write_cursor(conversation_id, msg_seq)
                    db.commit()
                processed += 1
                continue

            # 下文条数检查（enforce_window=True 时生效）
            if enforce_window:
                downstream_count = (
                    len(memorable_user_seqs) - bisect_right(memorable_user_seqs, msg_seq)
                )
                if downstream_count < WINDOW_SIZE:
                    logger.info(
                        f"[execute_pending_from_pool] 下文不足 ({downstream_count} < {WINDOW_SIZE})"
                        f"，停止处理: conv={conversation_id}, seq={msg_seq}"
                    )
                    break

            # 构建上下文
            with get_db_context() as db:
                repo = MemoryMessageRepository(db)
                context_before = [message_to_dict(m) for m in repo.build_context_before(conversation_id, msg_seq)]
                context_after = [message_to_dict(m) for m in repo.build_context_after(conversation_id, msg_seq)]

            await write_pipeline.run(
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

    logger.info(f"[execute_pending_from_pool] 完成: conv={conversation_id}, processed={processed}")

    if _MS.verify_unmark_safe(conversation_id):
        _MS.unmark_conversation_pending(conversation_id)

    return processed
