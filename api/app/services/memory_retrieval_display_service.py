"""记忆读取展示 Service

负责：
- 主检索问题的展示清洗（预处理后、问题拆分前）
- 从最终 MemorySearchResult 中筛选展示节点并聚合为单条读取卡片
- 读取卡片分页查询和前端 DTO 组装

聚合全部在内存中完成，不触碰数据库；写库由
``MemoryRetrievalDisplayQueue`` 的后台 consumer 执行。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, List

from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import to_timestamp_ms
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.memory_display_record_repository import (
    MemoryDisplayRecordRepository,
)
from app.schemas.memory_retrieval_display_schema import translate_search_mode

logger = logging.getLogger(__name__)

# 展示文本上限（字符）
QUERY_MAX_LENGTH = 200
SUMMARY_MAX_LENGTH = 200
ENTITY_MAX_LENGTH = 100

# 单张卡片最多展示的节点数
MAX_SUMMARY_COUNT = 3
MAX_ENTITY_COUNT = 3

_TRUNCATE_SUFFIX = "…"
_FILE_SUMMARY_TAG_RE = re.compile(
    r"<input-file-summary>.*?</input-file-summary>",
    flags=re.DOTALL | re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_display_text(text: Any, max_length: int) -> str:
    """折叠连续空白 → strip → 按上限截断并补省略号。"""
    if not text:
        return ""
    normalized = _WHITESPACE_RE.sub(" ", str(text)).strip()
    if not normalized:
        return ""
    if len(normalized) > max_length:
        return normalized[:max_length] + _TRUNCATE_SUFFIX
    return normalized


def clean_query_for_display(processed_query: str) -> str:
    """把预处理后的主检索问题清洗为可展示的 query。

    只做剥离文件摘要标签、空白规范化和截断，
    不做任何语义改写，也不使用问题拆分后的子问题。
    """
    if not processed_query:
        return ""
    without_file_summary = _FILE_SUMMARY_TAG_RE.sub(" ", processed_query)
    return normalize_display_text(without_file_summary, QUERY_MAX_LENGTH)


def aggregate_display_content(
    summaries: List[str],
    entities: List[str],
    language: str,
) -> str:
    """把入选的 Summary 正文和 Entity 名称聚合为卡片正文。

    - 单条 Summary 直接输出，多条按数字序号逐行输出；
    - Entity 以“相关内容 / Related”补充句附在正文之后；
    - 两者都为空时返回空串，调用方据此跳过投递。
    """
    parts: List[str] = []

    if len(summaries) == 1:
        parts.append(summaries[0])
    elif summaries:
        parts.append(
            "\n".join(f"{i}. {text}" for i, text in enumerate(summaries, start=1))
        )

    if entities:
        if language == "en":
            parts.append(f"Related: {', '.join(entities)}.")
        else:
            parts.append(f"相关内容：{'、'.join(entities)}。")

    return "\n".join(parts)


def build_retrieve_snapshot(
    result: Any,
    query: str,
    language: str,
) -> dict | None:
    """从最终检索结果构造一条可直接入库的 RETRIEVE 快照。

    Args:
        result: 最终 MemorySearchResult（不会被修改）
        query: 已清洗的主检索问题
        language: 检索发生时的 MemoryContext.language

    Returns:
        ``{"query": ..., "content": ...}``；没有可展示节点时返回 None。
    """
    from app.core.memory.enums import Neo4jNodeType

    memories = getattr(result, "memories", None) or []

    # 按 (节点类型, memory_id) 保留最高分，避免不同标签偶然复用 id 时误去重
    best: dict[tuple[str, str], tuple[float, Any]] = {}
    for memory in memories:
        source = getattr(memory, "source", None)
        if source not in (Neo4jNodeType.MEMORYSUMMARY, Neo4jNodeType.EXTRACTEDENTITY):
            continue

        data = getattr(memory, "data", None) or {}
        memory_id = str(data.get("id") or getattr(memory, "id", "") or "").strip()
        if not memory_id:
            logger.debug(
                "[RetrievalDisplay] 跳过缺少合法 id 的展示节点: source=%s", source
            )
            continue

        try:
            score = float(getattr(memory, "score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0

        key = (str(source), memory_id)
        if key not in best or score > best[key][0]:
            best[key] = (score, memory)

    scored_summaries: list[tuple[float, str]] = []
    scored_entities: list[tuple[float, str]] = []
    for score, memory in best.values():
        data = getattr(memory, "data", None) or {}
        if memory.source == Neo4jNodeType.MEMORYSUMMARY:
            text = normalize_display_text(data.get("content"), SUMMARY_MAX_LENGTH)
            if text:
                scored_summaries.append((score, text))
        else:
            text = normalize_display_text(data.get("name"), ENTITY_MAX_LENGTH)
            if text:
                scored_entities.append((score, text))

    # 分数降序，同分按文本升序保证输出稳定
    scored_summaries.sort(key=lambda item: (-item[0], item[1]))
    scored_entities.sort(key=lambda item: (-item[0], item[1]))

    summaries = [text for _, text in scored_summaries[:MAX_SUMMARY_COUNT]]

    # Entity 按清洗后的名称去重后取前几条
    seen_entity_keys: set[str] = set()
    top_entities: list[str] = []
    for _, text in scored_entities:
        key = text.casefold()
        if key in seen_entity_keys:
            continue
        seen_entity_keys.add(key)
        top_entities.append(text)
        if len(top_entities) >= MAX_ENTITY_COUNT:
            break

    # 已完整出现在入选 Summary 中的实体名不再重复进入补充句
    summary_blob = "\n".join(summaries).casefold()
    entities = [text for text in top_entities if text.casefold() not in summary_blob]

    content = aggregate_display_content(summaries, entities, language)
    if not content:
        return None
    return {"query": query, "content": content}


class MemoryRetrievalDisplayService:
    """读取展示业务逻辑层"""

    @staticmethod
    def query_retrieved(
        db: Session,
        end_user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        language: str,
        page: int,
        pagesize: int,
    ) -> tuple[List[dict], int] | None:
        """查询读取展示卡片并组装前端 DTO。

        一次检索已经是一行，直接按 occurred_at 分页，不再二次聚合。
        返回 None 表示终端用户不属于当前工作空间。
        """
        end_user_repo = EndUserRepository(db)
        if end_user_repo.get_active_end_user_in_workspace(
            end_user_id,
            workspace_id,
        ) is None:
            return None

        repo = MemoryDisplayRecordRepository(db)
        records, total = repo.query_retrieved_paginated(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
        )

        items = [
            {
                "id": str(record.id),
                "search_mode": translate_search_mode(record.search_mode, language),
                "query": record.query or "",
                "content": record.content,
                "occurred_at": to_timestamp_ms(record.occurred_at),
            }
            for record in records
        ]
        return items, total
