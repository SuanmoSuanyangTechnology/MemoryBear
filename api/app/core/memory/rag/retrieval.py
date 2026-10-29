"""RAG 记忆独立召回编排。

- 数据源：`rag_memory_current`（MemoryNodeType.RAG_MEMORY），不再经知识库检索器。
- 双路：`search_by_fulltext`（content，cjk）+ `search_by_embedding`（text_embedding）。
- 过滤：`end_user_id` 单条件 + `delete_at` 不存在（本期恒真）。
- 排序：未配 rerank 模型时本地融合（0.6 向量 + 0.4 关键词 + 双命中奖励）；
  配了则模型精排，精排失败降级为融合分，不阻断召回。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from langchain_core.documents import Document

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.exceptions import (
    MemoryModelType,
    MemoryRetrievalBusinessError,
    MemoryRetrievalImpact,
    MemoryRetrievalStage,
)
from app.core.memory.models.service_models import (
    Memory,
    MemoryContext,
    MemorySearchResult,
)
from app.core.memory.retrieval_trace.models import (
    RetrievalExecutionTrace,
    build_score_trace,
    finite_or_none,
    normalized_keyword_score,
)
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    StorageReadResult,
)
from app.core.memory.storage.models.dto import StorageItem
from app.core.memory.storage.service import MemoryStorageService, get_storage_service
from app.core.models import RedBearEmbeddings, RedBearRerank

logger = logging.getLogger(__name__)

RAG_LABEL = MemoryNodeType.RAG_MEMORY
DEFAULT_ALPHA = 0.6
DEFAULT_FULLTEXT_SCORE_THRESHOLD = 1.5
MAX_RERANK_CHARS_PER_DOC = 2000


def _build_rag_filter(end_user_id: str) -> NodeFilter:
    """用户隔离 + 未软删：与图侧检索同口径。"""
    return NodeFilter.all_of(
        FilterCondition(field="end_user_id", value=end_user_id),
        FilterCondition(
            field="delete_at",
            operator=FilterOperator.EXISTS,
            value=False,
        ),
    )


def _clamp01(value: Any) -> float:
    score = finite_or_none(value) or 0.0
    return min(1.0, max(0.0, score))


class RagRetrieval:
    """RAG 记忆召回器（单标签 RAG_MEMORY，无图关系能力）。"""

    def __init__(
        self,
        ctx: MemoryContext,
        *,
        embedder: RedBearEmbeddings | None = None,
        reranker: RedBearRerank | None = None,
        on_error: Callable[[MemoryRetrievalBusinessError], None] | None = None,
        storage: MemoryStorageService | None = None,
        semantic_degraded: bool = False,
        alpha: float = DEFAULT_ALPHA,
        fulltext_score_threshold: float = DEFAULT_FULLTEXT_SCORE_THRESHOLD,
    ) -> None:
        self.ctx = ctx
        self.embedder = embedder
        self.reranker = reranker
        self.on_error = on_error
        self.storage = storage or get_storage_service()
        self.semantic_degraded = semantic_degraded
        self.alpha = alpha
        self.fulltext_score_threshold = fulltext_score_threshold

    # ---------- 双路召回 ----------

    async def _fulltext(
        self, query: str, limit: int, node_filters: dict
    ) -> StorageReadResult:
        return await self.storage.search_by_fulltext(
            node_filters=node_filters,
            text=query,
            pre_limit=limit,
        )

    async def _embedding(
        self, query: str, limit: int, node_filters: dict
    ) -> StorageReadResult:
        if self.embedder is None:
            raise RuntimeError(
                "RagRetrieval._embedding requires an embedder; "
                "callers must skip the vector path when embedder is None"
            )
        embed = await self.embedder.aembed_query(query)
        return await self.storage.search_by_embedding(
            node_filters=node_filters,
            embed=embed,
            pre_limit=limit,
        )

    # ---------- 融合 ----------

    def _fuse(
        self,
        keyword_results: StorageReadResult,
        embedding_results: StorageReadResult,
    ) -> tuple[dict[str, dict], dict[str, float], dict[str, float]]:
        """按 id 合并两路，返回 文档字典 / 关键词分 / 向量分。"""
        documents: dict[str, dict] = {}
        kw_scores: dict[str, float] = {}
        emb_scores: dict[str, float] = {}

        for item in keyword_results.items:
            self._merge_item(item, documents)
            node_id = str(item.data.get("id") or "")
            kw_scores[node_id] = normalized_keyword_score(
                item.data.get("score"), self.fulltext_score_threshold
            )

        for item in embedding_results.items:
            self._merge_item(item, documents)
            node_id = str(item.data.get("id") or "")
            emb_scores[node_id] = _clamp01(item.data.get("score"))

        for node_id, data in documents.items():
            kw = kw_scores.get(node_id, 0.0)
            emb = emb_scores.get(node_id, 0.0)
            base = self.alpha * emb + (1 - self.alpha) * kw
            data["content_score"] = base + min(1 - base, 0.1 * kw * emb)
            data["kw_score"] = kw
            data["embedding_score"] = emb

        return documents, kw_scores, emb_scores

    @staticmethod
    def _merge_item(item: StorageItem, documents: dict[str, dict]) -> None:
        node_id = str(item.data.get("id") or "")
        if node_id and node_id not in documents:
            documents[node_id] = item.data.copy()

    # ---------- 重排 ----------

    async def _model_rerank(
        self, documents: list[Memory], query: str, limit: int
    ) -> tuple[bool, list[str]]:
        """模型精排；失败时保持融合分并标记降级。返回 (是否成功, 降级原因)。"""
        if not documents:
            return True, []

        rerank_documents = [
            Document(
                page_content=mem.content[:MAX_RERANK_CHARS_PER_DOC],
                metadata={"index": index},
            )
            for index, mem in enumerate(documents)
        ]
        try:
            reranked = await asyncio.to_thread(
                self.reranker.compress_documents,
                rerank_documents,
                query,
                top_n=limit,
            )
            score_by_index = {
                doc.metadata["index"]: doc.metadata.get("relevance_score", 0.0)
                for doc in reranked
            }
            for index, memory in enumerate(documents):
                if index in score_by_index:
                    memory.score = float(score_by_index[index])
                    if memory.retrieval_trace is not None:
                        memory.retrieval_trace.rerank_score = finite_or_none(
                            score_by_index[index]
                        )
                        memory.retrieval_trace.final_score = float(memory.score)
                        memory.retrieval_trace.rank_basis = "rerank_score"
            return True, []
        except Exception as exc:
            logger.warning("[RAGSearch] rerank failed, falling back to fusion: %s", exc)
            if self.on_error is not None:
                self.on_error(
                    MemoryRetrievalBusinessError.model_call_failed(
                        MemoryRetrievalStage.RERANK,
                        exc,
                        model_type=MemoryModelType.RERANK,
                        impact=MemoryRetrievalImpact.ORDERING_DEGRADED,
                    )
                )
            return False, ["rerank_failed"]

    # ---------- 对外入口 ----------

    async def hybrid_search(
        self, query: str, limit: int = 10, **kwargs: Any
    ) -> MemorySearchResult:
        limit = max(1, int(limit))
        node_filters = {RAG_LABEL: _build_rag_filter(str(self.ctx.end_user_id))}

        keyword_task = self._fulltext(query, limit, node_filters)
        if self.embedder is None or self.semantic_degraded:
            (keyword_results,) = await asyncio.gather(
                keyword_task, return_exceptions=True
            )
            embedding_results = StorageReadResult()
            semantic_skipped = True
        else:
            keyword_results, embedding_results = await asyncio.gather(
                keyword_task,
                self._embedding(query, limit, node_filters),
                return_exceptions=True,
            )
            semantic_skipped = False

        keyword_failed = isinstance(keyword_results, BaseException)
        semantic_failed = isinstance(embedding_results, BaseException)
        if keyword_failed:
            logger.warning("[RAGSearch] keyword search error: %s", keyword_results)
            keyword_results = StorageReadResult()
        if semantic_failed:
            logger.warning("[RAGSearch] embedding search error: %s", embedding_results)
            if self.on_error is not None:
                self.on_error(
                    MemoryRetrievalBusinessError.model_call_failed(
                        MemoryRetrievalStage.VECTOR_SEARCH,
                        embedding_results,
                        model_type=MemoryModelType.EMBEDDING,
                    )
                )
            embedding_results = StorageReadResult()

        documents, kw_scores, emb_scores = self._fuse(
            keyword_results, embedding_results
        )

        memories: list[Memory] = []
        for node_id, data in documents.items():
            memories.append(
                Memory(
                    score=float(data.get("content_score", 0.0)),
                    content=str(data.get("content") or ""),
                    data=data,
                    source=Neo4jNodeType.RAG,
                    query=query,
                    id=node_id,
                )
            )

        memories.sort(key=lambda item: item.score, reverse=True)

        rerank_status: str = "skipped"
        degraded_reasons: list[str] = []
        if self.reranker is not None and memories:
            rerank_ok, rerank_reasons = await self._model_rerank(
                memories, query, limit
            )
            rerank_status = "completed" if rerank_ok else "degraded"
            degraded_reasons.extend(rerank_reasons)
            if rerank_ok:
                memories.sort(key=lambda item: item.score, reverse=True)

        memories = memories[:limit]

        for memory in memories:
            memory.retrieval_trace = build_score_trace(
                node_id=memory.id,
                node_type=RAG_LABEL.value,
                final_score=memory.score,
                rank_basis=(
                    "rerank_score"
                    if rerank_status == "completed"
                    else "fusion_score"
                ),
                keyword_hit=memory.id in kw_scores,
                semantic_hit=memory.id in emb_scores,
                keyword_score=kw_scores.get(memory.id),
                semantic_score=emb_scores.get(memory.id),
                fusion_score=memory.data.get("content_score"),
                backend="rag",
                matched_queries=[query],
            )

        if keyword_failed:
            degraded_reasons.append("keyword_search_failed")
        if semantic_failed:
            degraded_reasons.append("semantic_search_failed")
        if self.semantic_degraded:
            degraded_reasons.append("reembedding_in_progress")

        return MemorySearchResult(
            memories=memories,
            execution_trace=RetrievalExecutionTrace(
                original_query=query,
                processed_query=query,
                backend="rag",
                limit=limit,
                keyword_status="failed" if keyword_failed else "completed",
                semantic_status=(
                    "skipped"
                    if semantic_skipped
                    else ("failed" if semantic_failed else "completed")
                ),
                rerank_status=rerank_status,
                keyword_hit_count=len(keyword_results.items),
                semantic_hit_count=len(embedding_results.items),
                raw_hit_count=(
                    len(keyword_results.items) + len(embedding_results.items)
                ),
                merged_count=len(memories),
                degraded_reasons=degraded_reasons,
            ),
        )

    async def keyword_search(
        self, query: str, limit: int = 10, **kwargs: Any
    ) -> MemorySearchResult:
        """RAG 没有独立全文通道语义，统一走双路。"""
        return await self.hybrid_search(query, limit, **kwargs)

    async def relation_search(self, query: str) -> MemorySearchResult:
        logger.info("[RAGSearch] RAG does not support relation search")
        return MemorySearchResult(memories=[])
