"""记忆召回内部追踪模型和分数构造工具。"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field


RetrievalType = Literal["keyword", "semantic", "hybrid", "provider"]
RankBasis = Literal[
    "keyword_score",
    "fusion_score",
    "source_adjusted_score",
    "rerank_score",
    "provider_score",
    "input_order",
]
BranchStatus = Literal["completed", "degraded", "failed", "skipped"]


class RetrievalScoreTrace(BaseModel):
    """单条候选的分数组成和命中来源。"""

    retrieval_type: RetrievalType
    keyword_score: float | None = None
    semantic_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None
    final_score: float
    rank_basis: RankBasis
    backend: Literal["neo4j", "rag"]
    node_type: str
    node_id: str
    matched_queries: list[str] = Field(default_factory=list)


class RetrievalExecutionTrace(BaseModel):
    """请求级分支状态；仅供验证接口投影，不进入通用序列化。"""

    original_query: str = ""
    processed_query: str = ""
    search_switch: str = ""
    backend: Literal["neo4j", "rag"] = "neo4j"
    limit: int = 10
    keyword_status: BranchStatus = "skipped"
    semantic_status: BranchStatus = "skipped"
    rerank_status: BranchStatus = "skipped"
    keyword_hit_count: int = 0
    semantic_hit_count: int = 0
    raw_hit_count: int = 0
    merged_count: int = 0
    degraded_reasons: list[str] = Field(default_factory=list)


def finite_or_none(value: Any) -> float | None:
    """保留 0 与缺失值的区别，并过滤 NaN、Infinity。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalized_keyword_score(raw_score: Any, threshold: float) -> float:
    """仅用于记录，不参与二次排序。

    WARNING: 本公式是 content_search.py Neo4jSearchService._normalize_kw_scores
    中归一化公式的副本（公式源头在那里，驱动真实排序）。两处必须同步修改：
    改动任一侧前，先 grep 另一侧，否则验证页展示的 keyword_score 将无法
    解释实际排序。后续计划统一为单一实现。
    """
    score = finite_or_none(raw_score) or 0.0
    return 1 / (1 + math.exp(-(score - threshold) / 2)) if score else 0.0


def diagnostic_fusion_score(keyword_score: float, semantic_score: float, alpha: float) -> float:
    """仅生成可观测值，不改变当前排序。

    WARNING: 本公式是 content_search.py Neo4jSearchService._rerank 中融合公式
    （含奖励项系数 0.1）的副本（公式源头在那里，驱动真实排序）。两处必须
    同步修改：改动任一侧前，先 grep 另一侧，否则验证页展示的 fusion_score
    将无法解释实际排序。后续计划统一为单一实现。
    """
    base = alpha * semantic_score + (1 - alpha) * keyword_score
    return base + min(1 - base, 0.1 * keyword_score * semantic_score)


def build_score_trace(
    *,
    node_id: str,
    node_type: str,
    final_score: Any,
    rank_basis: RankBasis,
    keyword_hit: bool = False,
    semantic_hit: bool = False,
    keyword_score: Any = None,
    semantic_score: Any = None,
    fusion_score: Any = None,
    rerank_score: Any = None,
    backend: Literal["neo4j", "rag"] = "neo4j",
    matched_queries: Iterable[str] = (),
) -> RetrievalScoreTrace:
    """根据已确定的召回结果构造轨迹，不重新计算最终排序。"""
    if backend == "rag":
        retrieval_type: RetrievalType = "provider"
    elif keyword_hit and semantic_hit:
        retrieval_type = "hybrid"
    elif semantic_hit:
        retrieval_type = "semantic"
    else:
        retrieval_type = "keyword"

    final = finite_or_none(final_score) or 0.0
    queries = list(dict.fromkeys(str(query) for query in matched_queries if str(query)))
    return RetrievalScoreTrace(
        retrieval_type=retrieval_type,
        keyword_score=finite_or_none(keyword_score) if keyword_hit else None,
        semantic_score=finite_or_none(semantic_score) if semantic_hit else None,
        fusion_score=finite_or_none(fusion_score),
        rerank_score=finite_or_none(rerank_score),
        final_score=final,
        rank_basis=rank_basis,
        backend=backend,
        node_type=node_type,
        node_id=str(node_id),
        matched_queries=queries,
    )
