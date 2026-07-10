"""
pruning_service — 语义剪枝编排服务

对滑动窗口覆盖的所有消息执行剪枝/规整：
- user: 规整（去除复制内容/长文压缩），找紧邻 assistant 配对辅助判断
- assistant: 独立压缩为记忆摘要

优先级：DB pruned_content 有值 → 直接用 → 否则调 LLM → 回写 DB。

WritePipeline 只需调用 prune_messages() 即可。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, List, NamedTuple, Optional, Tuple

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig

logger = logging.getLogger(__name__)


class PruneResult(NamedTuple):
    """单条消息 LLM 剪枝结果。"""

    pruned_content: str
    memory_type: str
    should_process_user_msg: bool
    processed_user_msg: Optional[str]
    processed_user_topic_entity_hint: Optional[str]


async def prune_messages(
    messages: List[dict],
    memory_config: "MemoryConfig",
    end_user_id: str,
    conversation_id: str = "",
    source: str = "",
    language: str = "zh",
) -> Tuple[List[dict], list]:
    """对滑动窗口覆盖的所有消息执行剪枝/规整。

    Args:
        messages: 滑动窗口覆盖的所有消息（context_before + target + context_after）
        memory_config: 记忆配置
        end_user_id: 终端用户 ID
        conversation_id: 对话 ID
        source: 写入来源（agent/service_api/mcp/workflow）
        language: 语言

    Returns:
        (pruned_messages, pruning_records) 元组：
        - pruned_messages: 处理后的消息列表（与输入等长）
        - pruning_records: 快照记录列表（按 message_seq 排序）
    """
    pruning_records: list = []

    if not messages:
        return [], pruning_records

    if not memory_config.pruning_enabled:
        return list(messages), pruning_records

    # Step 1: 从 DB 刷新 pruned_content
    _refresh_pruned_content(messages, conversation_id, end_user_id, source)

    # Step 2: 遍历消息，收集 LLM 任务
    result: List[Optional[dict]] = [None] * len(messages)
    prune_tasks: list = []
    db_updates: list = []

    # 初始化 LLM 客户端（懒加载，整个调用共享）
    llm_client = _get_llm_client(memory_config)

    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        seq = msg.get("message_seq", 0)
        content = msg.get("content", "")

        # 已有持久化结果 → 直接使用
        if msg.get("pruned_content") is not None:
            result[i] = {**msg, "content": msg["pruned_content"]}
            pruning_records.append({
                "conversation_id": conversation_id,
                "message_seq": seq,
                "source": "db",
                "type": f"{role}_pruning",
                "input": {"msgs": [{"role": role.title(), "msg": content}]},
                "output": {
                    "pruned_content": msg["pruned_content"],
                    "topic_entity_hint": msg.get("topic_entity_hint"),
                },
            })
            continue

        if role == "user":
            if _should_skip_user(content):
                result[i] = msg
                db_updates.append((seq, content, None))
                pruning_records.append({
                    "conversation_id": conversation_id,
                    "message_seq": seq,
                    "source": "skipped",
                    "type": "user_pruning",
                    "reason": f"无 <input-file-summary> 且长度 {len(content)} < 500",
                    "input": {"msgs": [{"role": "User", "msg": content}]},
                    "output": None,
                })
            else:
                pair = _next_assistant_content(messages, i)
                prune_tasks.append((i, "user", _call_llm_prune(
                    llm_client=llm_client,
                    memory_config=memory_config,
                    content=pair,
                    user_content=content,
                    language=language,
                )))
        elif role == "assistant":
            prune_tasks.append((i, "assistant", _call_llm_prune(
                llm_client=llm_client,
                memory_config=memory_config,
                content=content,
                user_content="",
                language=language,
            )))
        else:
            result[i] = msg

    # Step 3: 并发执行 LLM 任务
    if prune_tasks:
        coros = [coro for _, _, coro in prune_tasks]
        llm_results = await asyncio.gather(*coros, return_exceptions=True)

        for (idx, role_type, _), llm_result in zip(prune_tasks, llm_results):
            msg = messages[idx]
            seq = msg.get("message_seq", 0)
            content = msg.get("content", "")

            if isinstance(llm_result, BaseException):
                logger.warning(f"[Pruning] {role_type} 失败，截断原文兜底: seq={seq}, err={llm_result}")
                # LLM 失败 → 截断原文前 500 字符作为兜底，写入 DB 标记已处理
                fallback = content[:500]
                result[idx] = {**msg, "content": fallback}
                db_updates.append((seq, fallback, None))
            elif role_type == "user":
                pr: PruneResult = llm_result
                if pr.should_process_user_msg and pr.processed_user_msg and pr.processed_user_msg.strip():
                    result[idx] = {**msg, "content": pr.processed_user_msg}
                    db_updates.append((seq, pr.processed_user_msg, pr.processed_user_topic_entity_hint))
                else:
                    result[idx] = msg
                    db_updates.append((seq, content, None))
                pruning_records.append({
                    "conversation_id": conversation_id,
                    "message_seq": seq,
                    "source": "llm",
                    "type": "user_pruning",
                    "input": {"msgs": [
                        {"role": "User", "msg": content},
                        {"role": "Assistant", "msg": _next_assistant_content(messages, idx)},
                    ]},
                    "output": {
                        "should_process_user_msg": pr.should_process_user_msg,
                        "processed_user_msg": pr.processed_user_msg,
                        "processed_user_topic_entity_hint": pr.processed_user_topic_entity_hint,
                    },
                })
            elif role_type == "assistant":
                pr: PruneResult = llm_result
                to_write = pr.pruned_content if pr.pruned_content else content
                result[idx] = {**msg, "content": to_write}
                db_updates.append((seq, to_write, None))
                pruning_records.append({
                    "conversation_id": conversation_id,
                    "message_seq": seq,
                    "source": "llm",
                    "type": "assistant_pruning",
                    "input": {"msgs": [{"role": "Assistant", "msg": content}]},
                    "output": {
                        "assistant_memory_hint": pr.pruned_content,
                        "assistant_memory_type": pr.memory_type,
                    },
                })

    # Step 4: 批量回写 DB
    _batch_write_db(db_updates, conversation_id, end_user_id, source)

    return [m for m in result if m is not None], pruning_records


# ──────────────────────────────────────────────
# 内部函数
# ──────────────────────────────────────────────


def _refresh_pruned_content(
    messages: List[dict], conversation_id: str, end_user_id: str, source: str
) -> None:
    """从 DB 刷新 pruned_content 和 topic_entity_hint。"""
    null_seqs = [m.get("message_seq") for m in messages if m.get("pruned_content") is None and m.get("message_seq")]
    if not null_seqs:
        return
    try:
        from app.db import get_db_context
        from app.repositories.memory_message_repository import MemoryMessageRepository
        with get_db_context() as db:
            fresh = MemoryMessageRepository(db).batch_get_pruned_content(
                seqs=null_seqs,
                conversation_id=conversation_id or None,
                end_user_id=end_user_id,
                source=source,
            )
        for msg in messages:
            seq = msg.get("message_seq")
            if msg.get("pruned_content") is None and seq in fresh:
                data = fresh[seq]
                msg["pruned_content"] = data["pruned_content"]
                msg["topic_entity_hint"] = data.get("topic_entity_hint")
    except Exception as e:
        logger.warning(f"[Pruning] DB 刷新失败（不影响主流程）: {e}", exc_info=True)


def _batch_write_db(
    updates: list, conversation_id: str, end_user_id: str, source: str
) -> None:
    """批量回写 pruned_content 到 DB。"""
    if not updates:
        return
    try:
        from app.db import get_db_context
        from app.repositories.memory_message_repository import MemoryMessageRepository
        with get_db_context() as db:
            MemoryMessageRepository(db).batch_update_pruned_content(
                updates=updates,
                conversation_id=conversation_id or None,
                end_user_id=end_user_id,
                source=source,
            )
            db.commit()
    except Exception as e:
        logger.warning(f"[Pruning] DB 批量回写失败（不影响主流程）: {e}", exc_info=True)


def _get_llm_client(memory_config: "MemoryConfig"):
    """通过 ModelClientMixin 获取 LLM 客户端。"""
    from app.db import get_db_context
    from app.core.memory.pipelines.base_pipeline import ModelClientMixin
    with get_db_context() as db:
        return ModelClientMixin.get_llm_client(
            db,
            model_id=memory_config.llm_model_id,
            tenant_id=memory_config.tenant_id,
        )


async def _call_llm_prune(
    llm_client,
    memory_config: "MemoryConfig",
    content: str,
    user_content: str,
    language: str,
) -> PruneResult:
    """调用 SemanticPruner 执行单条消息剪枝。

    Args:
        llm_client: LLM 客户端实例
        memory_config: 记忆配置
        content: assistant 消息内容（user 角色调用时传入紧邻的 assistant 配对内容）
        user_content: user 消息内容（assistant 角色调用时传空字符串）
        language: 语言

    Returns:
        PruneResult 命名元组
    """
    from app.core.memory.storage_services.extraction_engine.data_preprocessing import SemanticPruner
    from app.core.memory.models.config_models import PruningConfig
    from app.core.memory.models.message_models import ConversationMessage

    pruning_config = PruningConfig(
        pruning_switch=memory_config.pruning_enabled,
        pruning_scene=memory_config.pruning_scene or "education",
        pruning_threshold=memory_config.pruning_threshold,
        scene_id=str(memory_config.scene_id) if memory_config.scene_id else None,
        ontology_class_infos=memory_config.ontology_class_infos,
    )

    pruner = SemanticPruner(
        config=pruning_config,
        llm_client=llm_client,
        language=language,
    )

    user_msg = ConversationMessage(role="user", msg=user_content if user_content else "[context]")
    asst_msg = ConversationMessage(role="assistant", msg=content)

    result = await pruner.extract_assistant_hint(user_msg, asst_msg)

    pruned = "" if result.assistant_memory_hint == "NULL" else result.assistant_memory_hint

    return PruneResult(
        pruned_content=pruned,
        memory_type=result.assistant_memory_type,
        should_process_user_msg=result.should_process_user_msg,
        processed_user_msg=result.processed_user_msg,
        processed_user_topic_entity_hint=result.processed_user_topic_entity_hint,
    )


def _should_skip_user(content: str) -> bool:
    """短消息且无 file-summary → 跳过 LLM 调用。"""
    return "<input-file-summary>" not in content and len(content) < 500


def _next_assistant_content(messages: List[dict], i: int) -> str:
    """找紧邻的下一条 assistant 消息内容。"""
    if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
        return messages[i + 1].get("content", "")
    return ""
