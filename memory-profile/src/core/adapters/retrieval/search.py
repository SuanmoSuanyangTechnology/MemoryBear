"""Retrieval sessions implementing :class:`ReadSearchSession`.

Ported from ``api/app/core/memory/read_services/search_engine/content_search.py``
(``Neo4jSearchService``) and ``api/app/core/memory/rag/retrieval.py``
(``RagRetrieval``). The merge/dedup/fusion rules are kept verbatim, including two
deliberate host behaviours that look surprising and must not be "fixed" here:

* the local-fusion hybrid path computes a per-type candidate quota but then
  truncates globally (``results[:limit]``); only the model-rerank path applies
  the entity quota, after scoring;
* identical content from the two branches keeps the keyword record first
  (``seen`` is filled from keyword results before embedding results).

The session never opens a database session itself: storage reads go through
:class:`ReadStorage`, PostgreSQL lookups through :class:`RetrievalQueries`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from src.core.adapters.retrieval.builders import MetadataBuilder, data_builder_factory
from src.core.adapters.retrieval.constants import (
    DEFAULT_ALPHA,
    DEFAULT_CONTENT_SCORE_THRESHOLD,
    DEFAULT_COSINE_SCORE_THRESHOLD,
    DEFAULT_FULLTEXT_SCORE_THRESHOLD,
    DEFAULT_INCLUDES,
    MAX_RERANK_CHARS_PER_DOC,
)
from src.core.adapters.retrieval.dedup import filter_quick_retrieval_memories
from src.core.adapters.retrieval.metadata import scoped_metadata
from src.core.adapters.retrieval.queries import RetrievalQueries
from src.core.adapters.retrieval.relation import RelationAgent
from src.core.adapters.storage import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    NodeProjection,
    ReadStorage,
    StorageItem,
    StorageReadResult,
    build_content_search_filters,
    merge_filters,
    projection_for,
)
from src.core.domain.enums import MemorySource
from src.core.domain.errors import (
    MemoryRetrievalBusinessError,
    RetrievalImpact,
    RetrievalModelType,
    RetrievalStage,
)
from src.core.domain.models import (
    MemoryItem,
    ReadContext,
    ReadResult,
    RelationItem,
    RetrievalExecutionTrace,
    build_score_trace,
    diagnostic_fusion_score,
    finite_or_none,
    normalized_keyword_score,
)
from src.core.domain.ports import (
    CandidateBatch,
    CandidateItem,
    ChatClient,
    CommunityRecallQuery,
    EmbeddingClient,
    ModelGateway,
    PromptRenderer,
    ReadSearchDeps,
    RerankClient,
)

logger = logging.getLogger(__name__)

ErrorCallback = Callable[[MemoryRetrievalBusinessError], None] | None


def _report_error(
    on_error: ErrorCallback,
    error: MemoryRetrievalBusinessError,
) -> None:
    if on_error is None:
        return
    try:
        on_error(error)
    except Exception:  # noqa: BLE001 - diagnostics must not break retrieval
        logger.warning("[Retrieval] on_error callback failed, ignored", exc_info=True)


def _source_value(source: MemorySource | str) -> str:
    return source.value if isinstance(source, MemorySource) else str(source)


def _clamp01(value: Any) -> float:
    score = finite_or_none(value) or 0.0
    return min(1.0, max(0.0, score))


class _BaseSearchSession:
    """Elements shared by the graph and RAG sessions."""

    def __init__(
        self,
        *,
        ctx: ReadContext,
        deps: ReadSearchDeps,
        includes: Sequence[str] | None,
        on_error: ErrorCallback,
        storage: ReadStorage,
        queries: RetrievalQueries,
        prompts: PromptRenderer,
        models: ModelGateway,
        cosine_score_threshold: float | None,
    ) -> None:
        self.ctx = ctx
        self.deps = deps
        self.on_error = on_error
        self.storage = storage
        self.queries = queries
        self.prompts = prompts
        self.models = models
        self.cosine_score_threshold = (
            DEFAULT_COSINE_SCORE_THRESHOLD
            if cosine_score_threshold is None
            else cosine_score_threshold
        )
        if includes is None:
            self.includes: tuple[str, ...] = DEFAULT_INCLUDES
        else:
            self.includes = tuple(dict.fromkeys(str(label) for label in includes))
        self._relation_agent = RelationAgent(
            queries=queries, prompts=prompts, end_user_id=ctx.end_user_id
        )

    # ── shared helpers ──────────────────────────────────────────

    def _content_filters(self) -> dict[str, NodeFilter]:
        return build_content_search_filters(self.includes, self.ctx.end_user_id)

    @staticmethod
    def _candidate_limit(
        node_type: str,
        limit: int,
        entity_limit: int | None,
    ) -> int:
        if node_type == MemorySource.EXTRACTED_ENTITY.value and entity_limit is not None:
            return min(limit, entity_limit)
        return limit

    @staticmethod
    def _apply_entity_quota(
        memories: list[MemoryItem],
        entity_limit: int | None,
    ) -> list[MemoryItem]:
        """Keep the first ``entity_limit`` entities of the current ranking."""

        if entity_limit is None:
            return memories
        kept: list[MemoryItem] = []
        entity_count = 0
        for memory in memories:
            if _source_value(memory.source) == MemorySource.EXTRACTED_ENTITY.value:
                if entity_count >= entity_limit:
                    continue
                entity_count += 1
            kept.append(memory)
        return kept

    def _apply_source_dedup(
        self, memories: list[MemoryItem]
    ) -> tuple[list[MemoryItem], dict[str, Any]]:
        """Deduplicate same-source candidates before the global truncation."""

        before_count = len(memories)
        kept, suppression_records = filter_quick_retrieval_memories(
            memories, end_user_id=str(self.ctx.end_user_id)
        )
        return kept, {
            "before_count": before_count,
            "after_count": len(kept),
            "suppression_records": suppression_records,
        }

    @staticmethod
    def _build_score_sidecar(
        keyword_results: StorageReadResult,
        embedding_results: StorageReadResult,
        *,
        alpha: float,
        fulltext_score_threshold: float,
    ) -> dict[tuple[str | None, str], dict[str, Any]]:
        """Keep keyword/semantic scores outside the recall records (diagnostics)."""

        sidecar: dict[tuple[str | None, str], dict[str, Any]] = {}
        for record in keyword_results.items:
            node_id = str(record.data.get("id") or "")
            if not node_id:
                continue
            item = sidecar.setdefault((record.label, node_id), {})
            item["keyword_hit"] = True
            item["keyword_score"] = normalized_keyword_score(
                record.data.get("score"), fulltext_score_threshold
            )
        for record in embedding_results.items:
            node_id = str(record.data.get("id") or "")
            if not node_id:
                continue
            item = sidecar.setdefault((record.label, node_id), {})
            item["semantic_hit"] = True
            item["semantic_score"] = finite_or_none(record.data.get("score")) or 0.0
        for item in sidecar.values():
            item["fusion_score"] = diagnostic_fusion_score(
                item.get("keyword_score", 0.0),
                item.get("semantic_score", 0.0),
                alpha,
            )
        return sidecar

    # ── perceptual content ─────────────────────────────────────

    async def resolve_perceptual_content(
        self,
        query: str,
        memories: list[MemoryItem],
        llm: ChatClient,
        model_view: Mapping[str, Any] | None,
    ) -> list[MemoryItem]:
        """Ask the multimodal model about the file behind each Perceptual memory.

        Keeps the stored summary (and the original item) when the formatting or
        model call fails, so a degraded file never breaks the read.
        """

        enhanced: list[MemoryItem] = []
        formatter = None
        if model_view is not None:
            try:
                formatter = await self.models.multimodal_formatter(self.ctx, model_view)
            except Exception:  # noqa: BLE001 - degrade to the stored summary
                logger.warning(
                    "[Perceptual] multimodal formatter unavailable; using stored summary",
                    exc_info=True,
                )
        for memory in memories:
            if _source_value(memory.source) != MemorySource.PERCEPTUAL.value:
                enhanced.append(memory)
                continue
            file_path = memory.data.get("file_path", "")
            if not file_path:
                enhanced.append(memory)
                continue
            try:
                parsed = await self._call_multimodal_for_query(
                    file_path=file_path,
                    file_name=memory.data.get("file_name", ""),
                    file_type=memory.data.get("file_type", ""),
                    perceptual_type=memory.data.get("perceptual_type", ""),
                    query=query,
                    llm=llm,
                    formatter=formatter,
                    on_error=self.on_error,
                )
                display_content = parsed.strip() if isinstance(parsed, str) else ""
                if display_content:
                    memory.data["_perceptual_display_content"] = display_content
                    memory.content = (
                        f"<history-file-input>\n"
                        f"<file-name>{memory.data.get('file_name', '')}</file-name>\n"
                        f"<file-summary>{memory.data.get('summary', '')}</file-summary>\n"
                        f"<file-analysis>{display_content}</file-analysis>\n"
                        f"</history-file-input>\n"
                    )
            except Exception as exc:  # noqa: BLE001 - keep the stored summary
                logger.warning(
                    "[Perceptual] multimodal parse failed file=%s: %s; using stored summary",
                    memory.data.get("file_name", ""),
                    exc,
                )
            enhanced.append(memory)
        return enhanced

    @staticmethod
    async def _call_multimodal_for_query(
        *,
        file_path: str,
        file_name: str,
        file_type: str,
        perceptual_type: str | int,
        query: str,
        llm: ChatClient,
        formatter: Any,
        on_error: ErrorCallback,
    ) -> str:
        """Format the file for the model and ask it a query-focused question."""

        if isinstance(perceptual_type, bool):
            return ""
        if isinstance(perceptual_type, int):
            perceptual_type_int = perceptual_type
        elif isinstance(perceptual_type, str) and perceptual_type.strip().isdigit():
            perceptual_type_int = int(perceptual_type.strip())
        else:
            return ""
        normalized_file_type = str(file_type or "").strip().lower()
        if perceptual_type_int == 1 and normalized_file_type == "image":
            file_input_type = "image"
        elif perceptual_type_int == 3 and normalized_file_type == "document":
            file_input_type = "document"
        else:
            return ""
        if formatter is None:
            return ""
        formatted = await formatter.format_files(
            [
                {
                    "type": file_input_type,
                    "transfer_method": "remote_url",
                    "url": file_path,
                    "file_type": file_type or "",
                }
            ],
            document_image_recognition=True,
            include_processing_errors=False,
        )
        if not formatted:
            return ""
        prompt = (
            f"请根据以下问题，分析文件 '{file_name}' 中相关的内容：\n\n"
            f"问题：{query}\n\n"
            f"请仅提取与问题直接相关的信息，以简洁的要点形式回答。"
        )
        try:
            response = await llm.ainvoke(
                [HumanMessage(content=[{"type": "text", "text": prompt}, *formatted])]
            )
        except Exception as exc:
            _report_error(
                on_error,
                MemoryRetrievalBusinessError.model_call_failed(
                    RetrievalStage.PERCEPTUAL_ANALYSIS,
                    exc,
                    model_type=RetrievalModelType.LLM,
                    impact=RetrievalImpact.INCOMPLETE,
                ),
            )
            raise
        return response.content if hasattr(response, "content") else str(response)

    # ── shared strategies ──────────────────────────────────────

    async def conversation_history(self) -> ReadResult:
        """RECENT strategy: recent dialogue turns from PostgreSQL."""

        history = await self.queries.conversation_history(
            end_user_id=self.ctx.end_user_id,
            conversation_id=self.ctx.conversation_id,
        )
        if history is None:
            return ReadResult(memories=[])
        messages = history.get("messages") or []
        content = "\n".join(
            f"{message.get('role')}:{message.get('content')}" for message in messages
        )
        return ReadResult(
            memories=[
                MemoryItem(
                    content=content,
                    source=MemorySource.HISTORY,
                    query="",
                    id=str(history.get("id", "")),
                    data={"messages": messages},
                )
            ]
        )


class Neo4jSearchSession(_BaseSearchSession):
    """Graph retrieval session (hybrid / keyword / relation / profile)."""

    def __init__(
        self,
        *,
        ctx: ReadContext,
        deps: ReadSearchDeps,
        includes: Sequence[str] | None,
        on_error: ErrorCallback,
        storage: ReadStorage,
        queries: RetrievalQueries,
        prompts: PromptRenderer,
        models: ModelGateway,
        cosine_score_threshold: float | None = None,
        alpha: float = DEFAULT_ALPHA,
        fulltext_score_threshold: float = DEFAULT_FULLTEXT_SCORE_THRESHOLD,
        content_score_threshold: float = DEFAULT_CONTENT_SCORE_THRESHOLD,
    ) -> None:
        super().__init__(
            ctx=ctx,
            deps=deps,
            includes=includes,
            on_error=on_error,
            storage=storage,
            queries=queries,
            prompts=prompts,
            models=models,
            cosine_score_threshold=cosine_score_threshold,
        )
        self.alpha = alpha
        self.fulltext_score_threshold = fulltext_score_threshold
        self.content_score_threshold = content_score_threshold
        self.semantic_degraded = deps.semantic_degraded

    # ── storage reads ──────────────────────────────────────────

    def _content_projections(self) -> dict[str, NodeProjection]:
        return {label: projection_for(label) for label in self.includes}

    async def _keyword_search(self, query: str, limit: int) -> StorageReadResult:
        return await self.storage.search_by_fulltext(
            self._content_filters(),
            text=query,
            pre_limit=limit,
            projections=self._content_projections(),
        )

    async def _embedding_search(self, query: str, limit: int) -> StorageReadResult:
        embedder: EmbeddingClient | None = self.deps.embedder
        if embedder is None:
            raise RuntimeError("embedding client unavailable")
        embedding = await embedder.aembed_query(query)
        return await self.storage.search_by_embedding(
            self._content_filters(),
            embedding=embedding,
            pre_limit=limit,
            projections=self._content_projections(),
        )

    # ── keyword search (EXPRESS) ───────────────────────────────

    async def keyword_search(
        self,
        query: str,
        limit: int = 10,
        *,
        entity_limit: int | None = None,
        apply_source_dedup: bool = False,
    ) -> ReadResult:
        """Full-text only: no embedding, rerank, relation search or splitting."""

        kw_results = await self._keyword_search(query, limit)
        all_records: list[StorageItem] = []
        limit_by_type = {
            node_type: self._candidate_limit(node_type, limit, entity_limit)
            for node_type in self.includes
        }
        for record in kw_results.items:
            remaining = limit_by_type.get(record.label)
            if remaining is None:
                all_records.append(record)
                continue
            if remaining > 0:
                all_records.append(record)
                limit_by_type[record.label] = remaining - 1

        if not all_records:
            return ReadResult(
                memories=[],
                execution_trace=RetrievalExecutionTrace(
                    keyword_status="completed",
                    semantic_status="skipped",
                    rerank_status="skipped",
                ),
            )

        all_records = self._normalize_kw_scores(all_records)
        for record in all_records:
            content_score = float(record.data.get("normalized_kw_score", 0))
            record.data["content_score"] = content_score
            record.data["score"] = content_score
        all_records.sort(key=lambda item: item.data["score"], reverse=True)

        memories: list[MemoryItem] = []
        for record in all_records:
            builder = data_builder_factory(record.label, record.data)
            memory = MemoryItem(
                score=builder.score,
                content=builder.content,
                data=builder.data,
                source=record.label,
                query=query,
                id=builder.id,
            )
            memory.retrieval_trace = build_score_trace(
                node_id=memory.id,
                node_type=record.label,
                final_score=memory.score,
                rank_basis="keyword_score",
                keyword_hit=True,
                keyword_score=record.data.get("normalized_kw_score"),
                matched_queries=[query],
            )
            memories.append(memory)

        dedup_summary: dict[str, Any] = {}
        if apply_source_dedup:
            memories, dedup_summary = self._apply_source_dedup(memories)
        memories = memories[:limit]

        return ReadResult(
            memories=memories,
            dedup_summary=dedup_summary,
            execution_trace=RetrievalExecutionTrace(
                keyword_status="completed",
                semantic_status="skipped",
                rerank_status="skipped",
                keyword_hit_count=kw_results.total,
                raw_hit_count=kw_results.total,
                merged_count=len(memories),
            ),
        )

    # ── hybrid search (DEEP / NORMAL / QUICK) ──────────────────

    async def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        *,
        entity_limit: int | None = None,
        apply_source_dedup: bool = False,
    ) -> ReadResult:
        kw_task = self._keyword_search(query, limit)
        if self.semantic_degraded:
            # Stored vectors are still being rebuilt for the active model:
            # skip the vector branch and rely on full-text only.
            (kw_results,) = await asyncio.gather(kw_task, return_exceptions=True)
            emb_results: Any = StorageReadResult()
        else:
            emb_task = self._embedding_search(query, limit)
            kw_results, emb_results = await asyncio.gather(
                kw_task, emb_task, return_exceptions=True
            )

        keyword_failed = isinstance(kw_results, BaseException)
        semantic_failed = isinstance(emb_results, BaseException)
        if keyword_failed:
            logger.warning("[MemorySearch] keyword search error: %s", kw_results)
            kw_results = StorageReadResult()
        if semantic_failed:
            logger.warning("[MemorySearch] embedding search error: %s", emb_results)
            _report_error(
                self.on_error,
                MemoryRetrievalBusinessError.model_call_failed(
                    RetrievalStage.VECTOR_SEARCH,
                    emb_results,
                    model_type=RetrievalModelType.EMBEDDING,
                ),
            )
            emb_results = StorageReadResult()

        score_sidecar = self._build_score_sidecar(
            kw_results,
            emb_results,
            alpha=self.alpha,
            fulltext_score_threshold=self.fulltext_score_threshold,
        )
        rerank_status = "skipped"
        degraded_reasons: list[str] = []
        if keyword_failed:
            degraded_reasons.append("keyword_search_failed")
        if semantic_failed:
            degraded_reasons.append("semantic_search_failed")
        if self.semantic_degraded:
            degraded_reasons.append("reembedding_in_progress")

        dedup_summary: dict[str, Any] = {}
        if self.deps.reranker is not None:
            (
                memories,
                rerank_status,
                rerank_reasons,
                dedup_summary,
            ) = await self._hybrid_search_with_model_rerank(
                kw_results,
                emb_results,
                query,
                limit,
                score_sidecar,
                entity_limit,
                apply_source_dedup,
            )
            degraded_reasons.extend(rerank_reasons)
        else:
            memories = self._local_fusion_memories(
                kw_results, emb_results, query, limit, score_sidecar
            )
            if apply_source_dedup:
                memories, dedup_summary = self._apply_source_dedup(memories)
            memories.sort(key=lambda item: item.score, reverse=True)
            memories = memories[:limit]

        semantic_status = (
            "degraded"
            if self.semantic_degraded
            else ("failed" if semantic_failed else "completed")
        )
        return ReadResult(
            memories=memories,
            dedup_summary=dedup_summary,
            execution_trace=RetrievalExecutionTrace(
                keyword_status="failed" if keyword_failed else "completed",
                semantic_status=semantic_status,  # type: ignore[arg-type]
                rerank_status=rerank_status,  # type: ignore[arg-type]
                keyword_hit_count=kw_results.total,
                semantic_hit_count=emb_results.total,
                raw_hit_count=kw_results.total + emb_results.total,
                merged_count=len(memories),
                degraded_reasons=degraded_reasons,
            ),
        )

    def _local_fusion_memories(
        self,
        keyword_results: StorageReadResult,
        embedding_results: StorageReadResult,
        query: str,
        limit: int,
        score_sidecar: dict[tuple[str | None, str], dict[str, Any]],
    ) -> list[MemoryItem]:
        """Local score fusion (no rerank slot configured)."""

        keyword_items = self._normalize_kw_scores(list(keyword_results.items))
        kw_norm_map = {
            item.data["id"]: float(item.data.get("normalized_kw_score", 0))
            for item in keyword_items
        }
        emb_norm_map = {
            item.data["id"]: float(item.data.get("score", 0))
            for item in embedding_results.items
        }
        combined: dict[str, dict] = {}
        label_map: dict[str, str] = {}
        for item in keyword_items:
            item_id = item.data["id"]
            combined[item_id] = item.data.copy()
            combined[item_id]["kw_score"] = kw_norm_map.get(item_id, 0)
            combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)
            label_map[item_id] = item.label
        for item in embedding_results.items:
            item_id = item.data["id"]
            if item_id in combined:
                combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)
            else:
                combined[item_id] = item.data.copy()
                combined[item_id]["kw_score"] = kw_norm_map.get(item_id, 0)
                combined[item_id]["embedding_score"] = emb_norm_map.get(item_id, 0)
                label_map[item_id] = item.label

        for item in combined.values():
            kw = float(item.get("kw_score", 0) or 0)
            emb = float(item.get("embedding_score", 0) or 0)
            base = self.alpha * emb + (1 - self.alpha) * kw
            item["content_score"] = base + min(1 - base, 0.1 * kw * emb)

        results = sorted(
            combined.values(), key=lambda item: item["content_score"], reverse=True
        )
        # Note: the host computes a per-type quota here but then truncates
        # globally, so the quota is intentionally not applied locally.
        results = results[:limit]

        memories: list[MemoryItem] = []
        for merged in results:
            node_type = label_map[merged["id"]]
            builder = data_builder_factory(node_type, merged)
            memory = MemoryItem(
                score=builder.score,
                content=builder.content,
                data=builder.data,
                source=node_type,
                query=query,
                id=builder.id,
            )
            score_info = score_sidecar.get((node_type, str(builder.id)), {})
            fusion_score = finite_or_none(merged.get("content_score"))
            rank_basis = (
                "source_adjusted_score"
                if fusion_score is not None and memory.score != fusion_score
                else "fusion_score"
            )
            memory.retrieval_trace = build_score_trace(
                node_id=memory.id,
                node_type=node_type,
                final_score=memory.score,
                rank_basis=rank_basis,  # type: ignore[arg-type]
                keyword_hit=bool(score_info.get("keyword_hit")),
                semantic_hit=bool(score_info.get("semantic_hit")),
                keyword_score=merged.get("kw_score"),
                semantic_score=merged.get("embedding_score"),
                fusion_score=fusion_score,
                matched_queries=[query],
            )
            memories.append(memory)
        return memories

    async def _hybrid_search_with_model_rerank(
        self,
        keyword_results: StorageReadResult,
        embedding_results: StorageReadResult,
        query: str,
        limit: int,
        score_sidecar: dict[tuple[str | None, str], dict[str, Any]],
        entity_limit: int | None,
        apply_source_dedup: bool,
    ) -> tuple[list[MemoryItem], str, list[str], dict[str, Any]]:
        seen: dict[str, StorageItem] = {}
        for record in keyword_results.items:
            node_id = record.data.get("id", "")
            if node_id and node_id not in seen:
                seen[node_id] = record
        for record in embedding_results.items:
            node_id = record.data.get("id", "")
            if node_id and node_id not in seen:
                seen[node_id] = record
        if not seen:
            return [], "skipped", [], {}

        memories: list[MemoryItem] = []
        for record in seen.values():
            builder = data_builder_factory(record.label, record.data)
            score_info = score_sidecar.get((record.label, str(builder.id)), {})
            memory = MemoryItem(
                score=builder.score,
                content=builder.content,
                data=builder.data,
                source=record.label,
                query=query,
                id=builder.id,
            )
            memory.retrieval_trace = build_score_trace(
                node_id=memory.id,
                node_type=record.label,
                final_score=memory.score,
                rank_basis="input_order",
                keyword_hit=bool(score_info.get("keyword_hit")),
                semantic_hit=bool(score_info.get("semantic_hit")),
                keyword_score=score_info.get("keyword_score"),
                semantic_score=score_info.get("semantic_score"),
                fusion_score=score_info.get("fusion_score"),
                matched_queries=[query],
            )
            memories.append(memory)

        # Source dedup must run before the model sees the candidates: suppressed
        # nodes should not consume rerank quota and then disappear afterwards.
        dedup_summary: dict[str, Any] = {}
        if apply_source_dedup:
            memories, dedup_summary = self._apply_source_dedup(memories)

        rerank_applied = False
        index_to_score: dict[int, float] = {}
        documents: list[Document] = []
        try:
            documents = [
                Document(
                    page_content=memory.content[:MAX_RERANK_CHARS_PER_DOC],
                    metadata={"index": index},
                )
                for index, memory in enumerate(memories)
            ]
            reranked: Any = []
            if documents:
                try:
                    # With an entity quota every candidate must be scored: the
                    # quota trims after scoring, so scoring only the top ``limit``
                    # would let zero-score candidates fill the freed slots.
                    requested_top_n = (
                        len(documents)
                        if entity_limit is not None
                        else min(limit, len(documents))
                    )
                    reranker: RerankClient | None = self.deps.reranker
                    if reranker is None:
                        raise RuntimeError("reranker unavailable")
                    reranked = await reranker.compress_documents(
                        documents, query, top_n=requested_top_n
                    )
                except Exception as exc:
                    _report_error(
                        self.on_error,
                        MemoryRetrievalBusinessError.model_call_failed(
                            RetrievalStage.RERANK,
                            exc,
                            model_type=RetrievalModelType.RERANK,
                            impact=RetrievalImpact.ORDERING_DEGRADED,
                        ),
                    )
                    raise
            try:
                index_to_score = {
                    doc.metadata["index"]: doc.metadata.get("relevance_score", 0.0)
                    for doc in reranked
                }
                for index, memory in enumerate(memories):
                    if index in index_to_score:
                        memory.score = index_to_score[index]
                        if memory.retrieval_trace is not None:
                            memory.retrieval_trace.rerank_score = finite_or_none(
                                index_to_score[index]
                            )
                            memory.retrieval_trace.final_score = float(memory.score)
                            memory.retrieval_trace.rank_basis = "rerank_score"
            except Exception as exc:
                _report_error(
                    self.on_error,
                    MemoryRetrievalBusinessError.structured_result_parse_failed(
                        RetrievalStage.RERANK,
                        exc,
                        model_type=RetrievalModelType.RERANK,
                        impact=RetrievalImpact.ORDERING_DEGRADED,
                    ),
                )
                raise
            rerank_applied = True
        except Exception as exc:  # noqa: BLE001 - fall back to fusion/input scores
            logger.warning(
                "[Neo4jSearch] Model rerank failed, falling back to content_score: %s",
                exc,
                exc_info=True,
            )
            index_to_score = {}

        degraded_reasons: list[str] = []
        rerank_status = "completed" if rerank_applied else "degraded"
        if not rerank_applied:
            degraded_reasons.append("rerank_failed")
        elif len(index_to_score) < min(limit, len(memories)):
            rerank_status = "degraded"
            degraded_reasons.append("rerank_partial_result")

        for memory in memories:
            if memory.retrieval_trace is not None:
                memory.retrieval_trace.final_score = float(memory.score)
        memories.sort(key=lambda item: item.score, reverse=True)
        memories = self._apply_entity_quota(memories, entity_limit)
        memories = memories[:limit]
        if rerank_applied:
            logger.info(
                "[Neo4jSearch] Model rerank applied: %s -> %s memories",
                len(documents),
                len(memories),
            )
        return memories, rerank_status, degraded_reasons, dedup_summary

    def _normalize_kw_scores(self, items: list[StorageItem]) -> list[StorageItem]:
        if not items:
            return []
        scores = [float(item.data.get("score", 0) or 0) for item in items]
        for item, score in zip(items, scores, strict=True):
            item.data["normalized_kw_score"] = normalized_keyword_score(
                score, self.fulltext_score_threshold
            )
        return items

    # ── relation search (DEEP) ─────────────────────────────────

    async def relation_search(self, query: str) -> ReadResult:
        llm = self.deps.llm
        if llm is None:
            raise RuntimeError("relation search requires a chat client")
        relations: list[RelationItem] = await self._relation_agent.run(
            query, llm, self.on_error
        )
        return ReadResult(memories=[], relations=relations)

    # ── community recall ───────────────────────────────────────

    async def recall_candidates(
        self,
        query: CommunityRecallQuery,
        value: str | list[float],
        *,
        limit: int,
    ) -> CandidateBatch:
        """Raw storage candidates for community/scene nodes (community strategy).

        The domain criteria are mapped onto storage labels and physical
        properties here, so the application layer never builds query filters.
        """

        label = (
            MemorySource.SCENE_SUMMARY
            if query.community_id is not None
            else MemorySource.SCENE_COMMUNITY
        )
        base = self._content_filters()
        if label.value not in base:
            raise ValueError("search filters must use included node types")
        node_filter = base[label.value]
        if query.community_id is not None:
            node_filter = merge_filters(
                node_filter,
                NodeFilter.all_of(
                    FilterCondition(
                        field="scene_community_id", value=query.community_id
                    ),
                    FilterCondition(field="community_status", value="ACTIVE"),
                    FilterCondition(field="community_eligibility", value="ELIGIBLE"),
                ),
            )
        scoped_filters = {label.value: node_filter}
        if isinstance(value, str):
            result = await self.storage.search_by_fulltext(
                scoped_filters, text=value, pre_limit=limit
            )
        else:
            result = await self.storage.search_by_embedding(
                scoped_filters, embedding=value, pre_limit=limit
            )
        return CandidateBatch(
            items=tuple(
                CandidateItem(label=item.label, data=item.data) for item in result.items
            ),
            backend=result.backend or "neo4j",
        )

    # ── profile (META / profile block) ─────────────────────────

    async def user_metadata(self) -> MemoryItem:
        """The L0 user-profile item used by every strategy."""

        record = await self.queries.user_metadata(self.ctx.end_user_id)
        scope = None
        if hasattr(self.queries, "metadata_scope"):
            # The read wire contract has no caller app yet; the lookup stays
            # conditional so app-aware calls can supply one later.
            scope = await self.queries.metadata_scope(
                self.ctx.end_user_id,
                self.ctx.app_id,
                tenant_id=self.ctx.tenant_id,
                workspace_id=self.ctx.workspace_id,
            )
        record = scoped_metadata(record, scope)
        scene_names = await self.queries.scene_names(
            record, self.ctx.workspace_id, tenant_id=self.ctx.tenant_id
        )
        builder = MetadataBuilder(record, scene_names)
        return MemoryItem(
            score=1,
            source=MemorySource.EXTRACTED_ENTITY,
            query="",
            id=self.ctx.end_user_id,
            content=builder.content,
            data=builder.data,
        )


class RagSearchSession(_BaseSearchSession):
    """RAG memory recall (Elasticsearch only, full-text + vector + rerank)."""

    def __init__(
        self,
        *,
        ctx: ReadContext,
        deps: ReadSearchDeps,
        includes: Sequence[str] | None,
        on_error: ErrorCallback,
        storage: ReadStorage,
        queries: RetrievalQueries,
        prompts: PromptRenderer,
        models: ModelGateway,
        cosine_score_threshold: float | None = None,
        alpha: float = DEFAULT_ALPHA,
        fulltext_score_threshold: float = DEFAULT_FULLTEXT_SCORE_THRESHOLD,
    ) -> None:
        super().__init__(
            ctx=ctx,
            deps=deps,
            includes=includes,
            on_error=on_error,
            storage=storage,
            queries=queries,
            prompts=prompts,
            models=models,
            cosine_score_threshold=cosine_score_threshold,
        )
        self.alpha = alpha
        self.fulltext_score_threshold = fulltext_score_threshold
        self.semantic_degraded = deps.semantic_degraded

    @staticmethod
    def _rag_filter(end_user_id: str) -> NodeFilter:
        return NodeFilter.all_of(
            FilterCondition(field="end_user_id", value=end_user_id),
            FilterCondition(field="delete_at", operator=FilterOperator.NOT_EXISTS),
        )

    def _fuse(
        self,
        keyword_results: StorageReadResult,
        embedding_results: StorageReadResult,
    ) -> tuple[dict[str, dict], dict[str, float], dict[str, float]]:
        documents: dict[str, dict] = {}
        kw_scores: dict[str, float] = {}
        emb_scores: dict[str, float] = {}
        for item in keyword_results.items:
            self._merge_item(item, documents)
            node_id = str(item.data.get("id") or "")
            if not node_id:
                logger.warning(
                    "[RAGRetrieval] keyword hit without id dropped: data_keys=%s",
                    sorted(item.data.keys()),
                )
                continue
            kw_scores[node_id] = normalized_keyword_score(
                item.data.get("score"), self.fulltext_score_threshold
            )
        for item in embedding_results.items:
            self._merge_item(item, documents)
            node_id = str(item.data.get("id") or "")
            if not node_id:
                logger.warning(
                    "[RAGRetrieval] embedding hit without id dropped: data_keys=%s",
                    sorted(item.data.keys()),
                )
                continue
            emb_scores[node_id] = _clamp01(item.data.get("score"))
        for node_id, data in documents.items():
            kw = kw_scores.get(node_id, 0.0)
            emb = emb_scores.get(node_id, 0.0)
            data["content_score"] = diagnostic_fusion_score(kw, emb, self.alpha)
            data["kw_score"] = kw
            data["embedding_score"] = emb
        return documents, kw_scores, emb_scores

    @staticmethod
    def _merge_item(item: StorageItem, documents: dict[str, dict]) -> None:
        node_id = str(item.data.get("id") or "")
        if node_id and node_id not in documents:
            documents[node_id] = item.data.copy()

    async def _model_rerank(
        self, memories: list[MemoryItem], query: str, limit: int
    ) -> tuple[bool, list[str]]:
        if not memories:
            return True, []
        documents = [
            Document(
                page_content=memory.content[:MAX_RERANK_CHARS_PER_DOC],
                metadata={"index": index},
            )
            for index, memory in enumerate(memories)
        ]
        reranker = self.deps.reranker
        if reranker is None:
            return False, ["rerank_failed"]
        try:
            reranked = await reranker.compress_documents(documents, query, top_n=limit)
            score_by_index = {
                doc.metadata.get("index"): doc.metadata.get("relevance_score", 0.0)
                for doc in reranked
                if isinstance(doc.metadata, dict) and doc.metadata.get("index") is not None
            }
            for index, memory in enumerate(memories):
                if index in score_by_index:
                    memory.score = float(score_by_index[index])
                    if memory.retrieval_trace is not None:
                        memory.retrieval_trace.rerank_score = finite_or_none(
                            score_by_index[index]
                        )
                        memory.retrieval_trace.final_score = float(memory.score)
                        memory.retrieval_trace.rank_basis = "rerank_score"
                else:
                    # Truncated by top_n: rerank and fusion scores live on
                    # different scales, so park these candidates at 0 instead of
                    # letting a high fusion score outrank a reranked document.
                    memory.score = 0.0
                    if memory.retrieval_trace is not None:
                        memory.retrieval_trace.final_score = 0.0
                        memory.retrieval_trace.rank_basis = "rerank_truncated"
            return True, []
        except Exception as exc:  # noqa: BLE001 - degrade to fusion order
            logger.warning("[RAGSearch] rerank failed, falling back to fusion: %s", exc)
            _report_error(
                self.on_error,
                MemoryRetrievalBusinessError.model_call_failed(
                    RetrievalStage.RERANK,
                    exc,
                    model_type=RetrievalModelType.RERANK,
                    impact=RetrievalImpact.ORDERING_DEGRADED,
                ),
            )
            return False, ["rerank_failed"]

    async def _embedding_search(
        self,
        node_filters: dict[str, NodeFilter],
        projections: dict[str, NodeProjection],
        query: str,
        limit: int,
    ) -> StorageReadResult:
        """Vector recall; an empty query vector is a skipped branch, not a failure.

        Host parity (``RagRetrieval._embedding``): a blank query makes the
        embedder return an empty vector, which the storage provider rejects.
        That means "nothing to look for", so it returns an empty result instead
        of surfacing as ``semantic_search_failed``.

        The embedder call lives here — and this coroutine is what the hybrid
        path gathers — so an embedding transport error degrades the semantic
        branch while the keyword hits still return.
        """

        embedder = self.deps.embedder
        if embedder is None:
            raise RuntimeError("embedding client unavailable")
        embedding = await embedder.aembed_query(query)
        if not embedding:
            logger.info("[RAGSearch] skip embedding path: empty query vector")
            return StorageReadResult()
        return await self.storage.search_by_embedding(
            node_filters,
            embedding=embedding,
            pre_limit=limit,
            projections=projections,
        )

    async def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        *,
        entity_limit: int | None = None,
        apply_source_dedup: bool = False,
    ) -> ReadResult:
        """RAG double recall + rerank fusion; graph-only knobs are ignored."""

        del entity_limit, apply_source_dedup
        limit = max(1, int(limit))
        node_filters = {MemorySource.RAG_MEMORY.value: self._rag_filter(str(self.ctx.end_user_id))}
        projections = {MemorySource.RAG_MEMORY.value: projection_for(MemorySource.RAG_MEMORY.value)}

        keyword_task = self.storage.search_by_fulltext(
            node_filters, text=query, pre_limit=limit, projections=projections
        )
        if self.deps.embedder is None or self.semantic_degraded:
            (keyword_results,) = await asyncio.gather(
                keyword_task, return_exceptions=True
            )
            embedding_results: Any = StorageReadResult()
            semantic_skipped = True
        else:
            embedding_task = self._embedding_search(
                node_filters, projections, query, limit
            )
            keyword_results, embedding_results = await asyncio.gather(
                keyword_task, embedding_task, return_exceptions=True
            )
            semantic_skipped = False

        # ``return_exceptions=True`` swallows everything, including process-level
        # control flow; those must propagate unchanged.
        for outcome in (keyword_results, embedding_results):
            if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
                raise outcome

        keyword_failed = isinstance(keyword_results, BaseException)
        semantic_failed = isinstance(embedding_results, BaseException)
        if semantic_failed:
            logger.warning(
                "[RAGSearch] embedding search error: %s",
                embedding_results,
                exc_info=(
                    type(embedding_results),
                    embedding_results,
                    embedding_results.__traceback__,
                ),
            )
            _report_error(
                self.on_error,
                MemoryRetrievalBusinessError.model_call_failed(
                    RetrievalStage.VECTOR_SEARCH,
                    embedding_results,
                    model_type=RetrievalModelType.EMBEDDING,
                ),
            )
            embedding_results = StorageReadResult()
        if keyword_failed:
            logger.error(
                "[RAGSearch] keyword search error: %s",
                keyword_results,
                exc_info=(
                    type(keyword_results),
                    keyword_results,
                    keyword_results.__traceback__,
                ),
            )
            _report_error(
                self.on_error,
                MemoryRetrievalBusinessError.model_call_failed(
                    RetrievalStage.KEYWORD_SEARCH,
                    keyword_results,
                    model_type=RetrievalModelType.EMBEDDING,
                    impact=RetrievalImpact.INCOMPLETE,
                ),
            )
            if semantic_skipped or semantic_failed:
                raise keyword_results
            keyword_results = StorageReadResult()

        documents, kw_scores, emb_scores = self._fuse(
            keyword_results, embedding_results
        )
        memories: list[MemoryItem] = [
            MemoryItem(
                score=float(data.get("content_score", 0.0)),
                content=str(data.get("content") or ""),
                data=data,
                # Host parity: RAG hits carry the public ``Rag`` source while the
                # storage label (``RagMemory``) only travels in retrieval_trace.
                source=MemorySource.RAG,
                query=query,
                id=node_id,
            )
            for node_id, data in documents.items()
        ]
        memories.sort(key=lambda item: item.score, reverse=True)
        # Base traces are built before rerank; ``_model_rerank`` updates them in
        # place so fusion scores are not overwritten afterwards.
        for memory in memories:
            memory.retrieval_trace = build_score_trace(
                node_id=memory.id,
                node_type=MemorySource.RAG_MEMORY.value,
                final_score=memory.score,
                rank_basis="fusion_score",
                keyword_hit=memory.id in kw_scores,
                semantic_hit=memory.id in emb_scores,
                keyword_score=kw_scores.get(memory.id),
                semantic_score=emb_scores.get(memory.id),
                fusion_score=memory.data.get("content_score"),
                backend="rag",
                matched_queries=[query],
            )

        rerank_status = "skipped"
        degraded_reasons: list[str] = []
        if self.deps.reranker is not None and memories:
            rerank_ok, rerank_reasons = await self._model_rerank(memories, query, limit)
            rerank_status = "completed" if rerank_ok else "degraded"
            degraded_reasons.extend(rerank_reasons)
            if rerank_ok:
                memories.sort(key=lambda item: item.score, reverse=True)
        memories = memories[:limit]

        if keyword_failed:
            degraded_reasons.append("keyword_search_failed")
        if semantic_failed:
            degraded_reasons.append("semantic_search_failed")
        if self.semantic_degraded:
            degraded_reasons.append("reembedding_in_progress")

        return ReadResult(
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
                ),  # type: ignore[arg-type]
                rerank_status=rerank_status,  # type: ignore[arg-type]
                keyword_hit_count=len(keyword_results.items),
                semantic_hit_count=len(embedding_results.items),
                raw_hit_count=len(keyword_results.items) + len(embedding_results.items),
                merged_count=len(documents),
                degraded_reasons=degraded_reasons,
            ),
        )

    async def keyword_search(
        self,
        query: str,
        limit: int = 10,
        *,
        entity_limit: int | None = None,
        apply_source_dedup: bool = False,
    ) -> ReadResult:
        """RAG has no standalone full-text channel; both paths run together."""

        return await self.hybrid_search(
            query,
            limit,
            entity_limit=entity_limit,
            apply_source_dedup=apply_source_dedup,
        )

    async def relation_search(self, query: str) -> ReadResult:
        logger.info("[RAGSearch] RAG does not support relation search")
        return ReadResult(memories=[])

    async def recall_candidates(
        self,
        query: CommunityRecallQuery,
        value: str | list[float],
        *,
        limit: int,
    ) -> CandidateBatch:
        raise RuntimeError("RAG storage does not support community recall")

    async def user_metadata(self) -> MemoryItem | None:
        """RAG workspaces have no graph profile.

        Host parity: ``MetaSearchService`` returns no profile memory at all for
        RAG storage, so this session reports the absence instead of fabricating
        a placeholder item that would leak into ``intermediate_outputs``.
        """

        return None


__all__ = [
    "Neo4jSearchSession",
    "RagSearchSession",
]