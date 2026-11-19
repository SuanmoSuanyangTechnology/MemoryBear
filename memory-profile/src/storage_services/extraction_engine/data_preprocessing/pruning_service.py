"""
pruning_service — 语义剪枝编排服务

对滑动窗口覆盖的所有消息执行剪枝/规整：
- user: 规整（去除复制内容/长文压缩），找紧邻 assistant 配对辅助判断
- assistant: 独立压缩为记忆摘要

优先级：DB pruned_content 有值 → 直接用 → 新模型（失败回退原 LLM）→ 按原规则回写 DB。

WritePipeline 只需调用 prune_messages() 即可。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, List, NamedTuple, Optional, Tuple

from src.storage_services.extraction_engine.data_preprocessing import SemanticPruner
from src.models.config_models import PruningConfig

if TYPE_CHECKING:
    from src.schemas.memory_config import MemoryConfig

logger = logging.getLogger(__name__)


class PruneResult(NamedTuple):
    """单条消息剪枝结果及来源。"""

    pruned_content: str
    memory_type: str
    should_process_user_msg: bool
    processed_user_msg: Optional[str]
    processed_user_topic_entity_hint: Optional[str]
    source: str = "llm"


async def prune_messages(
    messages: List[dict],
    memory_config: "MemoryConfig",
    end_user_id: str,
    conversation_id: str = "",
    source: str = "",
    language: str = "zh",
    persist: bool = False,
    llm_client=None,
    repository=None,
) -> Tuple[List[dict], list]:
    """对滑动窗口覆盖的所有消息执行剪枝/规整。

    Args:
        messages: 滑动窗口覆盖的所有消息（context_before + target + context_after）
        memory_config: 记忆配置
        end_user_id: 终端用户 ID
        conversation_id: 对话 ID
        source: 写入来源（agent/service_api/mcp/workflow）
        language: 语言
        persist: 是否将剪枝结果持久化到 DB。试运行(pilot)模式应传 False，
                 避免污染 PG 中的 pruned_content 字段。
        llm_client: Model client resolved and injected by the caller.
        repository: Required only when persistence is explicitly requested.

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
    if persist:
        if repository is None:
            raise ValueError("Persistent pruning requires a message repository")
        await _refresh_pruned_content(repository, messages, conversation_id, end_user_id, source)

    # Step 2: 遍历消息，收集剪枝任务
    result: List[Optional[dict]] = [None] * len(messages)
    prune_tasks: list = []
    db_updates: list = []

    # The calling service owns model resolution.
    if llm_client is None:
        raise ValueError("Pruning requires an injected LLM client")

    # 提前创建共享 PruningConfig + SemanticPruner（K 条消息复用同一实例）
    # 每个调用独立返回来源；统计计数不参与结果选择。
    shared_pruning_config = PruningConfig(
        pruning_switch=memory_config.pruning_enabled,
        pruning_threshold=memory_config.pruning_threshold,
    )
    shared_pruner = SemanticPruner(
        config=shared_pruning_config,
        llm_client=llm_client,
        language=language,
    )

    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        seq = msg.get("message_seq", 0)
        content = msg.get("content", "")

        # 已有持久化结果 → 直接使用
        if persist and msg.get("pruned_content") is not None:
            result[i] = {**msg, "content": msg["pruned_content"]}
            pruning_records.append(
                {
                    "conversation_id": conversation_id,
                    "message_seq": seq,
                    "source": "db",
                    "type": f"{role}_pruning",
                    "input": {"msgs": [{"role": role.title(), "msg": content}]},
                    "output": {
                        "pruned_content": msg["pruned_content"],
                        "topic_entity_hint": msg.get("topic_entity_hint"),
                    },
                }
            )
            continue

        if role == "user":
            pair = _next_assistant_content(messages, i)
            prune_tasks.append(
                (
                    i,
                    "user",
                    _call_llm_prune(
                        pruner=shared_pruner,
                        content=pair,
                        user_content=content,
                    ),
                )
            )
        elif role == "assistant":
            prune_tasks.append(
                (
                    i,
                    "assistant",
                    _call_llm_prune(
                        pruner=shared_pruner,
                        content=content,
                        user_content="",
                    ),
                )
            )
        else:
            result[i] = msg

    # Step 3: 沿用原有方式并发执行本批剪枝任务，不额外限制新模型并发。
    if prune_tasks:
        coros = [coro for _, _, coro in prune_tasks]
        llm_results = await asyncio.gather(*coros, return_exceptions=True)

        # A cancelled child must not become a cached 500-character fallback.
        for llm_result in llm_results:
            if isinstance(llm_result, asyncio.CancelledError):
                raise llm_result

        for (idx, role_type, _), llm_result in zip(prune_tasks, llm_results):
            msg = messages[idx]
            seq = msg.get("message_seq", 0)
            content = msg.get("content", "")

            if isinstance(llm_result, BaseException):
                logger.warning(
                    f"[Pruning] {role_type} 失败，截断原文兜底: seq={seq}, err={llm_result}"
                )
                # LLM 失败 → 截断原文前 500 字符作为兜底，写入 DB 标记已处理
                fallback = content[:500]
                result[idx] = {**msg, "content": fallback}
                db_updates.append((seq, fallback, None))
            elif role_type == "user":
                pr: PruneResult = llm_result
                if (
                    pr.should_process_user_msg
                    and pr.processed_user_msg
                    and pr.processed_user_msg.strip()
                ):
                    result[idx] = {**msg, "content": pr.processed_user_msg}
                    db_updates.append(
                        (seq, pr.processed_user_msg, pr.processed_user_topic_entity_hint)
                    )
                else:
                    result[idx] = msg
                    db_updates.append((seq, content, None))
                pruning_records.append(
                    {
                        "conversation_id": conversation_id,
                        "message_seq": seq,
                        "source": pr.source,
                        "type": "user_pruning",
                        "input": {
                            "msgs": [
                                {"role": "User", "msg": content},
                                {
                                    "role": "Assistant",
                                    "msg": _next_assistant_content(messages, idx),
                                },
                            ]
                        },
                        "output": {
                            "should_process_user_msg": pr.should_process_user_msg,
                            "processed_user_msg": pr.processed_user_msg,
                            "processed_user_topic_entity_hint": pr.processed_user_topic_entity_hint,
                        },
                    }
                )
            elif role_type == "assistant":
                pr: PruneResult = llm_result
                to_write = pr.pruned_content if pr.pruned_content else content
                result[idx] = {**msg, "content": to_write}
                db_updates.append((seq, to_write, None))
                pruning_records.append(
                    {
                        "conversation_id": conversation_id,
                        "message_seq": seq,
                        "source": pr.source,
                        "type": "assistant_pruning",
                        "input": {"msgs": [{"role": "Assistant", "msg": content}]},
                        "output": {
                            "assistant_memory_hint": pr.pruned_content,
                            "assistant_memory_type": pr.memory_type,
                        },
                    }
                )

    # Step 4: 批量回写 DB
    if persist:
        if db_updates:
            await repository.batch_update_pruned_content(
                updates=db_updates,
                conversation_id=conversation_id or None,
                end_user_id=end_user_id,
                source=source,
            )

    return [m for m in result if m is not None], pruning_records


# ──────────────────────────────────────────────
# 内部函数
# ──────────────────────────────────────────────


async def _call_llm_prune(
    pruner,
    content: str,
    user_content: str,
) -> PruneResult:
    """调用共用编排，保留原 LLM 的占位输入及返回转换。

    Args:
        pruner: 共享的 SemanticPruner 实例（协程安全，无可变状态）
        content: assistant 消息内容（user 角色调用时传入紧邻的 assistant 配对内容）
        user_content: user 消息内容（assistant 角色调用时传空字符串）

    Returns:
        PruneResult 命名元组
    """
    from src.models.message_models import ConversationMessage

    user_msg = ConversationMessage(role="user", msg=user_content)
    llm_user_msg = ConversationMessage(
        role="user", msg=user_content if user_content else "[context]"
    )
    asst_msg = ConversationMessage(role="assistant", msg=content)

    output = await pruner.normalize_messages(user_msg, asst_msg, llm_user_msg=llm_user_msg)
    result = output.response

    pruned = "" if result.assistant_memory_hint == "NULL" else result.assistant_memory_hint

    return PruneResult(
        pruned_content=pruned,
        memory_type=result.assistant_memory_type,
        should_process_user_msg=result.should_process_user_msg,
        processed_user_msg=result.processed_user_msg,
        processed_user_topic_entity_hint=result.processed_user_topic_entity_hint,
        source=output.source,
    )


def _next_assistant_content(messages: List[dict], i: int) -> str:
    """找紧邻的下一条 assistant 消息内容。"""
    if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
        return messages[i + 1].get("content", "")
    return ""


async def _refresh_pruned_content(repository, messages, conversation_id, end_user_id, source):
    """Refresh supplied messages through the caller's storage boundary."""
    seqs = [
        m.get("message_seq")
        for m in messages
        if m.get("pruned_content") is None and m.get("message_seq")
    ]
    if not seqs:
        return
    fresh = await repository.batch_get_pruned_content(
        seqs=seqs, conversation_id=conversation_id or None, end_user_id=end_user_id, source=source
    )
    for message in messages:
        data = fresh.get(message.get("message_seq"))
        if message.get("pruned_content") is None and data is not None:
            message["pruned_content"] = data["pruned_content"]
            message["topic_entity_hint"] = data.get("topic_entity_hint")
