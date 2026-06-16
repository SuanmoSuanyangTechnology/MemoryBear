"""
直接写入模块 — 不经过 memory_messages 表，从内存逐条写入 Neo4j

职责：
- Workflow MemoryWriteNode 路径
- API Service 路径
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def write_messages_to_rag(
    messages: List[dict],
    end_user_id: str,
    user_rag_memory_id: str,
) -> None:
    """将 messages 拼接为文本并写入 RAG 存储。

    供 _run_api_write（celery）和 _write_memory_locked（controller sync）共用。
    """
    from app.services.memory_konwledges_server import write_rag

    message_text = "\n".join([
        f"{(msg['role'] if isinstance(msg, dict) else msg.role)}: "
        f"{(msg['content'] if isinstance(msg, dict) else msg.content)}"
        for msg in messages
    ])
    await write_rag(end_user_id, message_text, user_rag_memory_id)


async def write_messages_direct(
    messages: List[dict],
    end_user_id: str,
    config_id: str = "",
    workspace_id: str = "",
    language: str = "zh",
    target_index: int = -1,
    conversation_id: str = "",
) -> Dict[str, Any]:
    """直接写入：不经过 memory_messages 表，逐条写入 Neo4j。

    workflow MemoryWriteNode 和 API Service 的统一入口。
    从内存 messages 数组构建上下文，串行逐条调用 WritePipeline 写入。

    两种模式：
    - target_index >= 0：只处理 messages[target_index] 这一条 user 消息，
      其前后消息自动作为上下文窗口。
    - target_index < 0（默认）：[DEPRECATED] 扫描 messages 中所有 user 消息，
      为每条预计算上下文窗口并逐一写入（兼容旧调用方）。
      将被废弃：单 task 全量处理，重试粒度粗，后续统一为单消息模式。

    Args:
        messages: 消息列表 [{"role": "user"|"assistant", "content": "...", ...}]
        end_user_id: 终端用户 ID
        config_id: 记忆配置 ID（UUID string 或 int）
        workspace_id: 工作空间 ID
        language: 语言 ("zh" | "en")
        target_index: 目标 user 消息在 messages 中的索引，-1 表示扫描全部
        conversation_id: 会话/批次 ID，用于聚合 assistant 剪枝节点到同一个 ConversationNode

    Returns:
        {"status": "success", "processed": N, "total": len(messages)}
    """
    from app.core.memory.pipelines.write_pipeline import WritePipeline
    from app.core.memory.sliding_window.window_utils import (
        precompute_context_windows,
        WINDOW_SIZE,
    )
    from app.services.memory_config_service import MemoryConfigService
    from app.db import get_db_context

    if not messages:
        logger.warning("[DirectWriter] write_messages_direct: messages 为空")
        return {"status": "success", "processed": 0, "total": 0}

    # 加载 memory_config
    with get_db_context() as db:
        config_service = MemoryConfigService(db)
        memory_config = config_service.load_memory_config(
            config_id=config_id,
            workspace_id=workspace_id,
            service_name="write_messages_direct",
        )
        if memory_config is None:
            raise RuntimeError(
                f"[DirectWriter] write_messages_direct 无法加载 memory_config: "
                f"config_id={config_id}"
            )

        pipeline = WritePipeline(
            memory_config=memory_config,
            end_user_id=end_user_id,
            language=language,
        )

        # ── 单消息模式：只处理 target_index 处的 user 消息 ──
        if target_index >= 0:
            if target_index >= len(messages):
                logger.error(
                    f"[DirectWriter] write_messages_direct target_index 越界: "
                    f"target_index={target_index}, len={len(messages)}"
                )
                return {"status": "success", "processed": 0, "total": len(messages)}

            target_msg = messages[target_index]
            if target_msg.get("role") != "user":
                logger.warning(
                    f"[DirectWriter] write_messages_direct target_index 指向非 user 消息: "
                    f"role={target_msg.get('role')}, index={target_index}"
                )
                return {"status": "success", "processed": 0, "total": len(messages)}

            user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
            # 找到 target_index 在 user_indices 中的位置
            user_pos = user_indices.index(target_index)

            # 前文：从第 (pos - WINDOW_SIZE) 个 user 开始，到 target_index 之前
            before_user_p = max(0, user_pos - WINDOW_SIZE)
            before_start = user_indices[before_user_p]
            context_before = messages[before_start:target_index]

            # 后文：从 target_index + 1 到第 (pos + WINDOW_SIZE) 个 user
            after_user_p = min(len(user_indices) - 1, user_pos + WINDOW_SIZE)
            after_end = user_indices[after_user_p] + 1
            context_after = messages[target_index + 1:after_end]

            try:
                await pipeline.run_with_window(
                    target_message=target_msg,
                    context_before=context_before,
                    context_after=context_after,
                    conversation_id=conversation_id,
                    message_seq=target_index + 1,
                    skip_cursor_advance=True,
                )
                logger.info(
                    f"[DirectWriter] write_messages_direct: "
                    f"end_user={end_user_id}, target_index={target_index}, processed=1"
                )
                return {"status": "success", "processed": 1, "total": len(messages)}
            except Exception as e:
                logger.error(
                    f"[DirectWriter] write_messages_direct 消息处理异常: "
                    f"end_user={end_user_id}, target_index={target_index}, err={e}",
                    exc_info=True,
                )
                return {"status": "success", "processed": 0, "total": len(messages)}

        # ── 批量模式：扫描所有 user 消息 ──
        context_windows = precompute_context_windows(messages)

        processed = 0
        for w in context_windows:
            msg = messages[w.index]

            try:
                await pipeline.run_with_window(
                    target_message=msg,
                    context_before=messages[w.before_start:w.before_end],
                    context_after=messages[w.after_start:w.after_end],
                    conversation_id=conversation_id,
                    message_seq=w.index + 1,
                    skip_cursor_advance=True,
                )
                processed += 1
                logger.info(
                    f"[DirectWriter] write_messages_direct: "
                    f"end_user={end_user_id}, seq={w.index + 1}, processed={processed}"
                )
            except Exception as e:
                logger.error(
                    f"[DirectWriter] write_messages_direct 消息处理异常，跳过: "
                    f"end_user={end_user_id}, index={w.index}, err={e}",
                    exc_info=True,
                )
                continue

        logger.info(
            f"[DirectWriter] write_messages_direct 完成: "
            f"end_user={end_user_id}, processed={processed}/{len(messages)}"
        )
        return {"status": "success", "processed": processed, "total": len(messages)}
