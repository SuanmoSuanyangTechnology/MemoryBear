"""QUICK 与 EXPRESS 检索结果的确定性后处理。"""

from __future__ import annotations

import json
import logging
from typing import Literal, TypedDict

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.models.service_models import Memory

logger = logging.getLogger(__name__)

# QUICK/EXPRESS 在跨类型排名前允许 ExtractedEntity 提供的候选数。
QUICK_SEARCH_ENTITY_LIMIT = 2
if (
    not isinstance(QUICK_SEARCH_ENTITY_LIMIT, int)
    or isinstance(QUICK_SEARCH_ENTITY_LIMIT, bool)
    or QUICK_SEARCH_ENTITY_LIMIT < 0
):
    raise ValueError("QUICK_SEARCH_ENTITY_LIMIT must be a non-negative integer")

SuppressionReason = Literal[
    "CHUNK_REPLACED_BY_SUMMARY",
    "STATEMENT_REPLACED_BY_SUMMARY",
    "CHUNK_REPLACED_BY_STATEMENT",
]


class SuppressionRecord(TypedDict):
    """单个被抑制节点的可追溯记录。"""

    node_type: str
    node_id: str
    reason: SuppressionReason
    source_chunk_id: str | None
    representative_ids: list[str]


def is_user_profile(memory: Memory, end_user_id: str) -> bool:
    """通过节点类型和用户 ID 识别合成画像，不依赖列表位置。"""
    return (
        memory.source == Neo4jNodeType.EXTRACTEDENTITY
        and memory.id == end_user_id
    )


def count_non_profile_memories(memories: list[Memory], end_user_id: str) -> int:
    """统计普通候选数量，不包含合成的用户画像节点。"""

    return sum(not is_user_profile(memory, end_user_id) for memory in memories)


def _chunk_id(value: object) -> str | None:
    """将来源 Chunk ID 归一化为非空字符串。"""

    if value is None:
        return None
    normalized = str(value)
    return normalized or None


def _summary_chunk_ids(memory: Memory) -> list[str]:
    """读取 Summary 的合法来源 Chunk ID；非列表字段按无来源处理。"""

    raw_chunk_ids = memory.data.get("chunk_ids", [])
    if not isinstance(raw_chunk_ids, list):
        return []
    return [chunk_id for value in raw_chunk_ids if (chunk_id := _chunk_id(value))]


def _append_representative(
    index: dict[str, list[str]],
    chunk_id: str,
    representative_id: str,
) -> None:
    """按首次出现顺序记录代表节点 ID，并避免重复记录。"""

    representatives = index.setdefault(chunk_id, [])
    if representative_id not in representatives:
        representatives.append(representative_id)


def filter_quick_retrieval_memories(
    memories: list[Memory],
    *,
    end_user_id: str,
) -> tuple[list[Memory], list[SuppressionRecord]]:
    """按来源优先级过滤，同时保持节点原相对顺序。"""

    summaries_by_chunk: dict[str, list[str]] = {}
    statements_by_chunk: dict[str, list[str]] = {}

    # 第一遍扫描完整输入，建立来源索引；代表节点可能出现在被抑制节点之后。
    for memory in memories:
        if memory.source == Neo4jNodeType.STATEMENT:
            chunk_id = _chunk_id(memory.data.get("chunk_id"))
            if chunk_id:
                _append_representative(statements_by_chunk, chunk_id, memory.id)
        elif memory.source == Neo4jNodeType.MEMORYSUMMARY:
            for chunk_id in _summary_chunk_ids(memory):
                _append_representative(summaries_by_chunk, chunk_id, memory.id)

    kept_memories: list[Memory] = []
    suppression_records: list[SuppressionRecord] = []

    def keep(memory: Memory) -> None:
        kept_memories.append(memory)

    def suppress(
        memory: Memory,
        reason: SuppressionReason,
        *,
        source_chunk_id: str | None,
        representative_ids: list[str],
    ) -> None:
        suppression_records.append({
            "node_type": memory.source.value,
            "node_id": memory.id,
            "reason": reason,
            "source_chunk_id": source_chunk_id,
            "representative_ids": list(representative_ids),
        })

    # 第二遍严格按输入顺序决策，保证保留节点和抑制记录的顺序确定。
    for memory in memories:
        if is_user_profile(memory, end_user_id):
            # 用户画像始终保留，且不占普通 Entity 名额。
            keep(memory)
        elif memory.source == Neo4jNodeType.MEMORYSUMMARY:
            keep(memory)
        elif memory.source == Neo4jNodeType.STATEMENT:
            chunk_id = _chunk_id(memory.data.get("chunk_id"))
            summary_ids = summaries_by_chunk.get(chunk_id, []) if chunk_id else []
            if summary_ids:
                suppress(
                    memory,
                    "STATEMENT_REPLACED_BY_SUMMARY",
                    source_chunk_id=chunk_id,
                    representative_ids=summary_ids,
                )
            else:
                keep(memory)
        elif memory.source == Neo4jNodeType.CHUNK:
            chunk_id = _chunk_id(memory.id)
            summary_ids = summaries_by_chunk.get(chunk_id, []) if chunk_id else []
            statement_ids = statements_by_chunk.get(chunk_id, []) if chunk_id else []
            if summary_ids:
                suppress(
                    memory,
                    "CHUNK_REPLACED_BY_SUMMARY",
                    source_chunk_id=chunk_id,
                    representative_ids=summary_ids,
                )
            elif statement_ids:
                suppress(
                    memory,
                    "CHUNK_REPLACED_BY_STATEMENT",
                    source_chunk_id=chunk_id,
                    representative_ids=statement_ids,
                )
            else:
                keep(memory)
        elif memory.source == Neo4jNodeType.EXTRACTEDENTITY:
            # Entity 类型配额已在跨类型排名前执行；后处理不再删除或记录 Entity。
            keep(memory)
        else:
            # Perceptual、Dialogue、Rag 等规则外节点原样透传。
            keep(memory)

    return kept_memories, suppression_records


def _write_quick_retrieval_dedup_log(
    *,
    operation_id: str,
    search_mode: str,
    before_count: int,
    after_count: int,
    suppression_records: list[SuppressionRecord],
) -> None:
    """序列化并写入单次检索的结构化去重日志。"""

    payload = {
        "event": "quick_retrieval_dedup",
        "operation_id": operation_id,
        "search_mode": search_mode,
        "before_count": before_count,
        "after_count": after_count,
        "suppressed_count": len(suppression_records),
        "suppression_records": suppression_records,
    }
    logger.info(
        "[QuickRetrievalDedup] %s",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


def log_quick_retrieval_dedup_safely(
    *,
    operation_id: str,
    search_mode: str,
    before_count: int,
    after_count: int,
    suppression_records: list[SuppressionRecord],
) -> None:
    """尽力写入日志；任何日志异常都不得改变检索结果。"""

    try:
        _write_quick_retrieval_dedup_log(
            operation_id=operation_id,
            search_mode=search_mode,
            before_count=before_count,
            after_count=after_count,
            suppression_records=suppression_records,
        )
    except Exception:
        # 完整日志失败后只记录最小上下文；降级日志失败时也必须吞掉异常。
        try:
            logger.warning(
                "[QuickRetrievalDedup] log failed and ignored: "
                "operation_id=%s search_mode=%s",
                operation_id,
                search_mode,
                exc_info=True,
            )
        except Exception:
            pass
