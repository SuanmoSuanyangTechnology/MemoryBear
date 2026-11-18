"""Retrieval pipeline: the core of a memory read.

Ported from the host's ``pipelines/memory_read.py`` (``ReadPipeLine``) with the
same strategy dispatch, merge semantics, stage emissions and degradation behavior.
Every external dependency is a port:

* ``ReadSearchFactory`` — Neo4j / RAG retrieval sessions;
* ``ModelGateway`` — chat / embedding / rerank clients and perceptual model views;
* ``ReadConfigPort`` — the semantic-rebuild (reembedding) state;
* ``RetrievalAlertPort`` — optional enterprise notification bridge.

The pipeline is pure orchestration: it never opens a database session, never
touches Redis and never writes side effects. Short-term memos and retrieval
display cards are the non-streaming use case's responsibility.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from src.core.application.read.community_search import (
    COMMUNITY_DEFAULTS,
    CommunitySearchService,
    timed_stage,
)
from src.core.application.read.projection import (
    profile_has_content,
    project_memory_items,
    project_profile_data,
    project_relation_items,
    project_result_items,
)
from src.core.application.read.stages import emit_memory_stage
from src.core.application.text.retrieval_text import (
    QueryPreprocessor,
    RetrievalSummaryProcessor,
    process_query,
)
from src.core.domain.enums import MemorySource, SearchStrategy, StorageType
from src.core.domain.errors import (
    CommunityRetrievalError,
    MemoryRetrievalBusinessError,
    ReadError,
    ReadErrorCode,
    ReadRequestError,
    RetrievalImpact,
)
from src.core.domain.errors import (
    MemoryRetrievalBusinessError as RetrievalDiagnostic,
)
from src.core.domain.models import (
    CommunityRetrievalTrace,
    MemoryItem,
    ReadContext,
    ReadOptions,
    ReadResult,
    RetrievalExecutionTrace,
)
from src.core.domain.ports import (
    ChatClient,
    EmbeddingClient,
    ModelGateway,
    ReadConfigPort,
    ReadSearchDeps,
    ReadSearchFactory,
    ReadSearchSession,
    RerankClient,
    RetrievalAlertPort,
)

logger = logging.getLogger(__name__)

DEFAULT_RETRIEVAL_LIMIT = 10
#: API ``quick_retrieval_dedup.QUICK_SEARCH_ENTITY_LIMIT``.
QUICK_SEARCH_ENTITY_LIMIT = 2

_CHAT_CLIENT: type = ChatClient


def _aggregate_branch_status(statuses: list[str]) -> str:
    if not statuses:
        return "skipped"
    if all(status == "skipped" for status in statuses):
        return "skipped"
    if all(status == "failed" for status in statuses):
        return "failed"
    if any(status in {"failed", "degraded"} for status in statuses):
        return "degraded"
    return "completed"


def _safe_merge_results(
    results: list[Any],
    label: str,
    *,
    on_error: Callable[[RetrievalDiagnostic], None],
) -> ReadResult:
    """Merge result list, skipping exceptions and recording a degraded branch."""

    merged = ReadResult(memories=[])
    for index, result in enumerate(results):
        if isinstance(result, Exception):
            if isinstance(result, MemoryRetrievalBusinessError):
                on_error(result.with_impact(RetrievalImpact.INCOMPLETE))
            logger.warning("[DeepRead] %s search error (question #%s): %s", label, index, result)
        elif isinstance(result, ReadResult):
            merged = merged + result
    return merged


def _merge_execution_traces(
    results: list[Any], *, merged_count: int
) -> RetrievalExecutionTrace:
    """Aggregate multi-query execution status; it never drives result ordering."""

    traces = [
        result.execution_trace
        for result in results
        if isinstance(result, ReadResult) and result.execution_trace is not None
    ]
    reasons = [reason for trace in traces for reason in trace.degraded_reasons]
    has_failed_subquery = any(isinstance(result, Exception) for result in results)
    if has_failed_subquery:
        reasons.append("subquery_failed")
    keyword_status = _aggregate_branch_status(
        [trace.keyword_status for trace in traces]
    )
    semantic_status = _aggregate_branch_status(
        [trace.semantic_status for trace in traces]
    )
    if has_failed_subquery:
        keyword_status = "failed" if keyword_status == "skipped" else "degraded"
        semantic_status = "failed" if semantic_status == "skipped" else "degraded"
    return RetrievalExecutionTrace(
        keyword_status=keyword_status,  # type: ignore[arg-type]
        semantic_status=semantic_status,  # type: ignore[arg-type]
        rerank_status=_aggregate_branch_status(  # type: ignore[arg-type]
            [trace.rerank_status for trace in traces]
        ),
        keyword_hit_count=sum(trace.keyword_hit_count for trace in traces),
        semantic_hit_count=sum(trace.semantic_hit_count for trace in traces),
        raw_hit_count=sum(trace.raw_hit_count for trace in traces),
        merged_count=merged_count,
        degraded_reasons=list(dict.fromkeys(reasons)),
    )


def _attach_matched_queries(final_result: ReadResult, source_results: list[Any]) -> None:
    """Attach the queries a kept candidate matched, without changing dedup/merge."""

    query_map: dict[tuple[str, str], list[str]] = {}
    for result in source_results:
        if not isinstance(result, ReadResult):
            continue
        for memory in result.memories:
            if memory.query:
                key = (_source_value(memory.source), str(memory.id))
                query_map.setdefault(key, []).append(str(memory.query))
    for memory in final_result.memories:
        trace = memory.retrieval_trace
        if trace is None:
            continue
        key = (_source_value(memory.source), str(memory.id))
        trace.matched_queries = list(
            dict.fromkeys(query_map.get(key, trace.matched_queries))
        )


def _source_value(source: MemorySource | str) -> str:
    return source.value if isinstance(source, MemorySource) else str(source)


class ReadPipeline:
    """One retrieval run for a single read context."""

    def __init__(
        self,
        *,
        ctx: ReadContext,
        search_factory: ReadSearchFactory,
        config_port: ReadConfigPort,
        model_gateway: ModelGateway,
        prompts: Any,
        alerts: RetrievalAlertPort | None = None,
        degrade_cache: Any | None = None,
        today: Callable[[], str] | None = None,
    ) -> None:
        self.ctx = ctx
        self._search_factory = search_factory
        self._config_port = config_port
        self._model_gateway = model_gateway
        self._prompts = prompts
        self._alerts = alerts
        self._degrade_cache = degrade_cache
        self._today = today or _default_today
        self._query_preprocessor = QueryPreprocessor(
            prompts=prompts, today=self._today
        )
        self._summary_processor = RetrievalSummaryProcessor(prompts=prompts)
        self._embedding_client: EmbeddingClient | None = None
        self._llm_client: ChatClient | None = None
        self._vision_llm_client: ChatClient | None = None
        self._audio_llm_client: ChatClient | None = None
        self._rerank_client: RerankClient | None = None
        self._meta_search: ReadSearchSession | None = None
        self._run_started_at = 0.0
        self._notification_error: MemoryRetrievalBusinessError | None = None
        self._retrieval_operation_id = ""

    # ── public entry ────────────────────────────────────────────

    async def run(
        self,
        query: str,
        search_switch: SearchStrategy,
        history: list[Any] | None = None,
        options: ReadOptions | None = None,
        retrieval_operation_id: str | None = None,
    ) -> ReadResult:
        options = options or ReadOptions()
        history = history or []
        started_at = time.perf_counter()
        self._run_started_at = started_at
        self._retrieval_operation_id = retrieval_operation_id or uuid.uuid4().hex
        self._notification_error = None
        original_query = query
        query = (
            query.strip()
            if search_switch == SearchStrategy.COMMUNITY
            else process_query(query)
        )
        if search_switch in {
            SearchStrategy.DEEP,
            SearchStrategy.NORMAL,
            SearchStrategy.QUICK,
            SearchStrategy.EXPRESS,
        }:
            self._emit_stage(
                "query_preprocessed",
                {
                    "original_query": original_query[:2000],
                    "processed_query": query[:2000],
                    "will_split": search_switch
                    in {SearchStrategy.DEEP, SearchStrategy.NORMAL},
                },
            )

        try:
            match search_switch:
                case SearchStrategy.COMMUNITY:
                    result = await self._community_read(
                        query,
                        options.limit,
                        options.enable_rerank,
                        history,
                        options=options,
                    )
                case SearchStrategy.DEEP:
                    result = await self._deep_read(
                        query,
                        history,
                        options.limit,
                        options=options,
                    )
                case SearchStrategy.NORMAL:
                    result = await self._normal_read(
                        query,
                        history,
                        options.limit,
                        options=options,
                    )
                case SearchStrategy.QUICK:
                    result = await self._quick_read(
                        query, options.limit, options=options
                    )
                case SearchStrategy.EXPRESS:
                    result = await self._express_read(
                        query, options.limit, options=options
                    )
                case SearchStrategy.RECENT:
                    return await self._conv_history()
                case SearchStrategy.META:
                    result = await self._user_meta()
                case _:
                    raise RuntimeError("Unsupported search strategy")
        except Exception:
            # Side-channel alert only; re-raise the original retrieval error.
            if self._notification_error is not None:
                await self._enqueue_retrieval_alert(self._notification_error)
            raise

        if search_switch in {SearchStrategy.QUICK, SearchStrategy.EXPRESS} and result.dedup_summary:
            logger.info(
                "[ReadPipeline] dedup operation_id=%s mode=%s before=%s after=%s",
                self._retrieval_operation_id,
                search_switch.name.lower(),
                result.dedup_summary.get("before_count", 0),
                result.dedup_summary.get("after_count", 0),
            )

        if search_switch in {
            SearchStrategy.QUICK,
            SearchStrategy.EXPRESS,
            SearchStrategy.META,
        }:
            self._emit_result_ready(result, started_at)

        if self._notification_error is not None:
            await self._enqueue_retrieval_alert(self._notification_error)

        if result.execution_trace is None:
            result.execution_trace = RetrievalExecutionTrace()
        result.execution_trace.original_query = original_query
        result.execution_trace.processed_query = query
        result.execution_trace.search_switch = search_switch.value
        result.execution_trace.backend = self.ctx.storage_type.value  # type: ignore[assignment]
        result.execution_trace.limit = (
            COMMUNITY_DEFAULTS.evidence_top_k
            if search_switch == SearchStrategy.COMMUNITY
            else options.limit
        )
        return result

    # ── strategy implementations ────────────────────────────────

    async def _community_read(
        self,
        query: str,
        limit: int,
        enable_rerank: bool,
        history: list[Any],
        *,
        options: ReadOptions,
    ) -> ReadResult:
        cfg = self.ctx.config
        trace = CommunityRetrievalTrace(
            parameters=COMMUNITY_DEFAULTS.as_dict(),
            model_config_id=str(getattr(cfg, "embedding_model_id", "") or "") if cfg else "",
            ignored_compatibility_parameters={
                "limit": limit,
                "includes": list(options.includes) if options.includes else None,
                "skip_summary": options.skip_summary,
                "enable_rerank": enable_rerank,
                "history_count": len(history),
            },
        )
        service: CommunitySearchService | None = None
        try:
            with timed_stage(trace, "initialization"):
                if self.ctx.storage_type != StorageType.NEO4J:
                    raise CommunityRetrievalError("unsupported_storage")
                if not query:
                    raise CommunityRetrievalError("empty_query")
                if cfg is None or not cfg.workspace_id or not cfg.embedding_model_id:
                    raise CommunityRetrievalError("missing_model_configuration")
                not_ready = await self._config_port.semantic_rebuild_pending(
                    workspace_id=cfg.workspace_id,
                    end_user_id=self.ctx.end_user_id,
                    embedding_model_id=cfg.embedding_model_id,
                )
                trace.semantic_degraded = not_ready
                if not not_ready:
                    trace.model_name = await self._model_gateway.model_name(
                        cfg.embedding_model_id, cfg.tenant_id
                    )
                embedder = None if not_ready else await self._get_embedding_client()
                recall_session = self._search_factory.create(
                    self.ctx,
                    includes=(
                        MemorySource.SCENE_COMMUNITY.value,
                        MemorySource.SCENE_SUMMARY.value,
                    ),
                    deps=ReadSearchDeps(
                        embedder=embedder, semantic_degraded=not_ready
                    ),
                    on_error=self._record_retrieval_error,
                    cosine_score_threshold=COMMUNITY_DEFAULTS.community_min_cosine,
                )
            service = CommunitySearchService(
                recall_candidates=recall_session.recall_candidates,
                end_user_id=self.ctx.end_user_id,
                embedder=embedder,
                model_config_id=trace.model_config_id,
                model_name=trace.model_name,
                operation_id=self._retrieval_operation_id,
                semantic_degraded=not_ready,
            )
            service.trace = trace
            return await service.search(
                query,
                compatibility_parameters=trace.ignored_compatibility_parameters,
            )
        except Exception as exc:
            reason = (
                str(exc)
                if isinstance(exc, CommunityRetrievalError)
                else "scan_timeout"
                if isinstance(exc, TimeoutError)
                else "dependency_failed"
                if service is not None
                else "initialization_failed"
            )
            if service is None:
                trace.status = "failed"
                trace.error_reason = reason
                logger.warning(
                    "[CommunityRead] initialization failed operation_id=%s reason=%s",
                    self._retrieval_operation_id,
                    reason,
                )
            if reason in {"empty_query", "unsupported_storage"}:
                raise ReadRequestError(
                    f"Invalid memory read request: {reason}", reason=reason
                ) from exc
            raise ReadError(
                "Memory read failed",
                code=ReadErrorCode.MEMORY_READ_FAILED,
                reason=reason,
            ) from exc

    async def _deep_read(
        self,
        query: str,
        history: list[Any],
        limit: int,
        *,
        options: ReadOptions,
    ) -> ReadResult:
        self._ensure_run_started()
        search_service = await self._get_search_service(
            options.includes, enable_rerank=options.enable_rerank
        )
        memory_l0 = await self._load_profile(options.include_metadata)
        profile = project_profile_data(memory_l0)
        self._emit_stage(
            "profile_loaded",
            {"has_profile": profile_has_content(profile), "profile": profile},
        )
        questions = await self._query_preprocessor.split(
            query,
            history,
            memory_l0.content,
            await self._get_llm_client(),
            on_error=self._record_retrieval_error,
        )
        self._emit_stage(
            "query_split",
            {
                "count": len(questions),
                "questions": [str(question)[:100] for question in questions[:5]],
            },
        )
        tasks: list[Any] = []
        for question in questions:
            tasks.append(search_service.hybrid_search(question, limit))
            tasks.append(search_service.relation_search(question))
        all_results = list(await asyncio.gather(*tasks, return_exceptions=True))
        hybrid_results = all_results[::2]
        relation_results = all_results[1::2]

        hybrid_search_res = _safe_merge_results(
            hybrid_results, "hybrid", on_error=self._record_retrieval_error
        )
        relation_res = _safe_merge_results(
            relation_results, "relation", on_error=self._record_retrieval_error
        )
        hybrid_search_res.memories.sort(key=lambda item: item.score, reverse=True)
        _attach_matched_queries(hybrid_search_res, hybrid_results)
        hybrid_execution_trace = _merge_execution_traces(
            hybrid_results, merged_count=len(hybrid_search_res.memories)
        )
        relation_res.relations = list(relation_res.relations)
        self._emit_stage(
            "hybrid_searched",
            {
                "hit_count": self._raw_memory_count(hybrid_results),
                "memory_count": len(hybrid_search_res.memories),
                "shown_count": min(3, len(hybrid_search_res.memories)),
                "items": project_memory_items(hybrid_search_res.memories, limit=3),
            },
        )
        self._emit_stage(
            "relation_searched",
            {
                "hit_count": self._raw_relation_count(relation_results),
                "relation_count": len(relation_res.relations),
                "shown_count": min(3, len(relation_res.relations)),
                "items": project_relation_items(relation_res.relations, limit=3),
            },
        )

        perceptual_memories = [
            memory
            for memory in hybrid_search_res.memories
            if _source_value(memory.source) == MemorySource.PERCEPTUAL.value
        ]
        perceptual_memory_count = len(perceptual_memories)
        if perceptual_memories:
            await self._resolve_perceptual_memories(
                questions,
                hybrid_results,
                hybrid_search_res,
                search_service,
            )

        results = hybrid_search_res + relation_res
        merged_memory_count = len(results.memories)
        merged_relation_count = len(results.relations)
        results.memories.sort(key=lambda item: item.score, reverse=True)
        final_items = project_result_items(memory_l0, results, limit=5)
        perceptual_items = [
            item
            for item in final_items
            if item.get("memory_type") == "file" and item.get("source") == "Perceptual"
        ]
        if perceptual_memory_count > 0:
            self._emit_stage(
                "perceptual_processed",
                {
                    "memory_count": perceptual_memory_count,
                    "shown_count": len(perceptual_items),
                    "items": perceptual_items,
                },
            )
        self._emit_stage(
            "results_merged",
            {
                "memory_count": merged_memory_count,
                "relation_count": merged_relation_count,
            },
        )
        self._emit_stage(
            "results_ranked",
            {"count": len(results.memories), "order": "score_desc"},
        )
        if not options.skip_summary:
            results.content_str = await self._summary_processor.summary(
                query,
                results.content,
                memory_l0.content if memory_l0 else "",
                await self._get_llm_client(),
                on_error=self._record_retrieval_error,
            )
        self._emit_stage(
            "context_prepared", {"memory_count": len(results.memories)}
        )
        combined = memory_l0 + results
        combined.execution_trace = hybrid_execution_trace
        self._emit_stage(
            "result_ready",
            {
                "duration_ms": self._elapsed_ms(),
                "total_count": len(combined.memories),
                "shown_count": len(final_items),
                "items": final_items,
            },
        )
        return combined

    async def _resolve_perceptual_memories(
        self,
        questions: list[str],
        hybrid_results: list[Any],
        hybrid_search_res: ReadResult,
        search_service: ReadSearchSession,
    ) -> None:
        """Resolve perceptual file memories for the sub-questions that produced them."""

        parse_tasks: list[Any] = []
        type_client_cache: dict[int, tuple[ChatClient, Any] | None] = {}

        async def clients_for_type(perceptual_type: int) -> tuple[ChatClient, Any] | None:
            if perceptual_type not in type_client_cache:
                try:
                    llm = await self._get_perceptual_llm_client(perceptual_type)
                    view = (
                        await self._get_perceptual_model_view(perceptual_type)
                        if llm is not None
                        else None
                    )
                    type_client_cache[perceptual_type] = (llm, view) if view is not None else None
                except Exception:
                    logger.warning(
                        "[DeepRead] Unable to initialize perceptual model for type %s; "
                        "using stored summary",
                        perceptual_type,
                        exc_info=True,
                    )
                    type_client_cache[perceptual_type] = None
            return type_client_cache[perceptual_type]

        for index, question in enumerate(questions):
            if index >= len(hybrid_results) or isinstance(hybrid_results[index], Exception):
                continue
            sub_perceptual = [
                memory
                for memory in hybrid_results[index].memories
                if _source_value(memory.source) == MemorySource.PERCEPTUAL.value
            ]
            if not sub_perceptual:
                continue
            by_type: dict[int, list[MemoryItem]] = {}
            for memory in sub_perceptual:
                raw_type = memory.data.get("perceptual_type", 0)
                if isinstance(raw_type, int) and not isinstance(raw_type, bool):
                    perceptual_type = raw_type
                elif isinstance(raw_type, str) and raw_type.strip().isdigit():
                    perceptual_type = int(raw_type.strip())
                else:
                    logger.warning(
                        "[DeepRead] Invalid perceptual_type %r for memory %s; "
                        "using stored summary",
                        raw_type,
                        memory.id,
                    )
                    continue
                if perceptual_type not in {1, 2, 3}:
                    logger.warning(
                        "[DeepRead] Unsupported perceptual_type %r for memory %s; "
                        "using stored summary",
                        raw_type,
                        memory.id,
                    )
                    continue
                file_type = str(memory.data.get("file_type") or "").strip().lower()
                if (perceptual_type, file_type) not in {(1, "image"), (3, "document")}:
                    continue
                by_type.setdefault(perceptual_type, []).append(memory)

            for perceptual_type, memories in by_type.items():
                clients = await clients_for_type(perceptual_type)
                if clients is None:
                    continue
                llm, model_view = clients
                parse_tasks.append(
                    search_service.resolve_perceptual_content(
                        question, memories, llm, model_view
                    )
                )

        if not parse_tasks:
            return
        parse_results = await asyncio.gather(*parse_tasks, return_exceptions=True)
        for parse_result in parse_results:
            if isinstance(parse_result, Exception):
                continue
            for memory in parse_result:
                for existing in hybrid_search_res.memories:
                    if existing.id == memory.id:
                        existing.content = memory.content
                        display_content = memory.data.get("_perceptual_display_content")
                        if display_content:
                            existing.data["_perceptual_display_content"] = display_content
                        break

    async def _normal_read(
        self,
        query: str,
        history: list[Any],
        limit: int,
        *,
        options: ReadOptions,
    ) -> ReadResult:
        self._ensure_run_started()
        search_service = await self._get_search_service(
            options.includes, enable_rerank=options.enable_rerank
        )
        memory_l0 = await self._load_profile(options.include_metadata)
        profile = project_profile_data(memory_l0)
        self._emit_stage(
            "profile_loaded",
            {"has_profile": profile_has_content(profile), "profile": profile},
        )
        questions = await self._query_preprocessor.split(
            query,
            history,
            memory_l0.content,
            await self._get_llm_client(),
            on_error=self._record_retrieval_error,
        )
        self._emit_stage(
            "query_split",
            {
                "count": len(questions),
                "questions": [str(question)[:100] for question in questions[:5]],
            },
        )
        all_results = list(
            await asyncio.gather(
                *(search_service.hybrid_search(question, limit) for question in questions),
                return_exceptions=True,
            )
        )
        results = _safe_merge_results(
            all_results, "normal", on_error=self._record_retrieval_error
        )
        results.memories.sort(key=lambda item: item.score, reverse=True)
        _attach_matched_queries(results, all_results)
        results.execution_trace = _merge_execution_traces(
            all_results, merged_count=len(results.memories)
        )
        self._emit_stage(
            "hybrid_searched",
            {
                "hit_count": self._raw_memory_count(all_results),
                "memory_count": len(results.memories),
                "shown_count": min(3, len(results.memories)),
                "items": project_memory_items(results.memories, limit=3),
            },
        )
        self._emit_stage(
            "results_merged",
            {"memory_count": len(results.memories), "relation_count": 0},
        )
        results.memories.sort(key=lambda item: item.score, reverse=True)
        self._emit_stage(
            "results_ranked",
            {"count": len(results.memories), "order": "score_desc"},
        )
        if not options.skip_summary:
            results.content_str = await self._summary_processor.summary(
                query,
                results.content,
                memory_l0.content if memory_l0 else "",
                await self._get_llm_client(),
                on_error=self._record_retrieval_error,
            )
        self._emit_stage(
            "context_prepared", {"memory_count": len(results.memories)}
        )
        combined = memory_l0 + results
        combined.execution_trace = results.execution_trace
        items = project_result_items(memory_l0, results, limit=5)
        self._emit_stage(
            "result_ready",
            {
                "duration_ms": self._elapsed_ms(),
                "total_count": len(combined.memories),
                "shown_count": len(items),
                "items": items,
            },
        )
        return combined

    async def _express_read(
        self, query: str, limit: int, *, options: ReadOptions
    ) -> ReadResult:
        """Full-text only: no embedding, relation search, splitting or summary."""

        includes = options.includes or (
            MemorySource.CHUNK.value,
            MemorySource.STATEMENT.value,
            MemorySource.EXTRACTED_ENTITY.value,
            MemorySource.DIALOGUE.value,
        )
        meta_task = asyncio.ensure_future(
            self._load_profile(options.include_metadata)
        )
        search_service = await self._get_search_service(
            includes, need_embedder=False, need_llm=False
        )
        express_res = await search_service.keyword_search(
            query,
            limit,
            entity_limit=QUICK_SEARCH_ENTITY_LIMIT,
            apply_source_dedup=True,
        )
        memory_l0 = await meta_task
        profile = project_profile_data(memory_l0)
        self._emit_stage(
            "profile_loaded",
            {"has_profile": profile_has_content(profile), "profile": profile},
        )
        combined = memory_l0 + express_res
        combined.execution_trace = express_res.execution_trace
        combined.dedup_summary = express_res.dedup_summary
        return combined

    async def _quick_read(
        self, query: str, limit: int, *, options: ReadOptions
    ) -> ReadResult:
        meta_task = asyncio.create_task(self._load_profile(options.include_metadata))
        search_service = await self._get_search_service(
            options.includes, need_llm=False, enable_rerank=options.enable_rerank
        )
        quick_res = await search_service.hybrid_search(
            query,
            limit,
            entity_limit=QUICK_SEARCH_ENTITY_LIMIT,
            apply_source_dedup=True,
        )
        memory_l0 = await meta_task
        profile = project_profile_data(memory_l0)
        self._emit_stage(
            "profile_loaded",
            {"has_profile": profile_has_content(profile), "profile": profile},
        )
        combined = memory_l0 + quick_res
        combined.execution_trace = quick_res.execution_trace
        combined.dedup_summary = quick_res.dedup_summary
        return combined

    async def _conv_history(self) -> ReadResult:
        return await (await self._meta_session()).conversation_history()

    async def _user_meta(self) -> ReadResult:
        session = await self._meta_session()
        item = await session.user_metadata()
        return ReadResult(memories=[] if item is None else [item])

    async def _load_profile(self, include_metadata: bool) -> ReadResult:
        """Load the user-profile metadata node, unless the caller excluded it."""

        if not include_metadata:
            return ReadResult(memories=[])
        return await self._user_meta()

    # ── dependency construction ─────────────────────────────────

    async def _get_llm_client(self) -> ChatClient:
        if self._llm_client is None:
            self._llm_client = await self._model_gateway.chat(self.ctx)
        return self._llm_client

    async def _get_embedding_client(self) -> EmbeddingClient | None:
        if self._embedding_client is None:
            cfg = self.ctx.config
            if cfg is None or not cfg.embedding_model_id:
                return None
            self._embedding_client = await self._model_gateway.embedding(self.ctx)
        return self._embedding_client

    async def _get_rerank_client(self) -> RerankClient | None:
        cfg = self.ctx.config
        if cfg is None or cfg.rerank_model_id is None:
            return None
        if self._rerank_client is None:
            self._rerank_client = await self._model_gateway.rerank(self.ctx)
        return self._rerank_client

    async def _get_perceptual_llm_client(
        self, perceptual_type: int
    ) -> ChatClient | None:
        cfg = self.ctx.config
        if cfg is None:
            return None
        if perceptual_type == 1:
            if not cfg.vision_model_id:
                return None
            if self._vision_llm_client is None:
                self._vision_llm_client = await self._model_gateway.perceptual_chat(
                    self.ctx, perceptual_type
                )
            return self._vision_llm_client
        if perceptual_type == 2:
            if not cfg.audio_model_id:
                return None
            if self._audio_llm_client is None:
                self._audio_llm_client = await self._model_gateway.perceptual_chat(
                    self.ctx, perceptual_type
                )
            return self._audio_llm_client
        if perceptual_type == 3:
            return await self._get_llm_client()
        return None

    async def _get_perceptual_model_view(self, perceptual_type: int) -> Any:
        cfg = self.ctx.config
        if cfg is None:
            return None
        model_id = {
            1: cfg.vision_model_id,
            2: cfg.audio_model_id,
            3: cfg.llm_model_id,
        }.get(perceptual_type)
        if model_id is None:
            return None
        return await self._model_gateway.perceptual_model_view(
            self.ctx, perceptual_type
        )

    async def _should_degrade_semantic(self) -> bool:
        """Skip vector search while stored vectors are rebuilt for a new model."""

        cfg = self.ctx.config
        if cfg is None or cfg.workspace_id is None or cfg.embedding_model_id is None:
            return False
        from src.core.adapters.read.degrade_cache import key_for

        cache_key = key_for(
            end_user_id=self.ctx.end_user_id,
            workspace_id=cfg.workspace_id,
            embedding_model_id=cfg.embedding_model_id,
        )
        if self._degrade_cache is not None:
            cached = await self._degrade_cache.get(cache_key)
            if cached is not None:
                return cached
        try:
            pending = await self._config_port.semantic_rebuild_pending(
                workspace_id=cfg.workspace_id,
                end_user_id=self.ctx.end_user_id,
                embedding_model_id=cfg.embedding_model_id,
            )
            if self._degrade_cache is not None:
                await self._degrade_cache.set(cache_key, pending)
            return pending
        except Exception:
            logger.warning(
                "[ReadPipeline] degrade check failed, falling back to no degrade",
                exc_info=True,
            )
            return False

    async def _build_retrieval_deps(
        self,
        need_embedder: bool,
        enable_rerank: bool,
        embedder: EmbeddingClient | None = None,
    ) -> tuple[EmbeddingClient | None, bool, RerankClient | None]:
        if need_embedder and embedder is None:
            embedder = await self._get_embedding_client()
        semantic_degraded = (
            await self._should_degrade_semantic() if need_embedder else False
        )
        if need_embedder and embedder is None:
            logger.warning(
                "[ReadPipeline] embedding client unavailable, "
                "vector search disabled for this query"
            )
            semantic_degraded = True
        reranker = None
        if enable_rerank:
            reranker = await self._get_rerank_client()
            if reranker is None:
                logger.warning(
                    "[ReadPipeline] enable_rerank=True but rerank slot not configured, "
                    "falling back to score fusion"
                )
        return embedder, semantic_degraded, reranker

    async def _get_search_service(
        self,
        includes: tuple[str, ...] | None = None,
        *,
        need_embedder: bool = True,
        need_llm: bool = True,
        enable_rerank: bool = False,
    ) -> ReadSearchSession:
        embedder: EmbeddingClient | None = None
        llm: ChatClient | None = None
        if need_embedder and need_llm:
            embedder, llm = await asyncio.gather(
                self._get_embedding_client(),
                self._get_llm_client(),
            )
        elif need_llm:
            llm = await self._get_llm_client()
        embedder, semantic_degraded, reranker = await self._build_retrieval_deps(
            need_embedder, enable_rerank, embedder=embedder
        )
        return self._search_factory.create(
            self.ctx,
            includes=includes,
            deps=ReadSearchDeps(
                embedder=embedder,
                llm=llm,
                reranker=reranker,
                semantic_degraded=semantic_degraded,
            ),
            on_error=self._record_retrieval_error,
        )

    async def _meta_session(self) -> ReadSearchSession:
        if self._meta_search is None:
            self._meta_search = self._search_factory.create(
                self.ctx,
                includes=None,
                deps=ReadSearchDeps(),
                on_error=self._record_retrieval_error,
            )
        return self._meta_search

    # ── diagnostics ─────────────────────────────────────────────

    def _record_retrieval_error(self, error: MemoryRetrievalBusinessError) -> None:
        if self._notification_error is None:
            self._notification_error = error

    async def _enqueue_retrieval_alert(
        self, error: MemoryRetrievalBusinessError
    ) -> None:
        if self._alerts is None:
            return
        cfg = self.ctx.config
        try:
            await self._alerts.notify(
                error,
                operation_id=self._retrieval_operation_id,
                tenant_id=str(getattr(cfg, "tenant_id", "") or ""),
                workspace_id=str(getattr(cfg, "workspace_id", "") or ""),
                end_user_id=str(self.ctx.end_user_id),
            )
        except Exception:
            logger.exception(
                "[ReadPipeline] retrieval alert escaped safe wrapper; ignored "
                "operation_id=%s",
                self._retrieval_operation_id,
            )

    # ── stage helpers ───────────────────────────────────────────

    def _emit_stage(self, stage: str, data: dict[str, Any]) -> None:
        emit_memory_stage(stage, data)

    @staticmethod
    def _raw_memory_count(results: list[Any]) -> int:
        return sum(
            len(result.memories) for result in results if isinstance(result, ReadResult)
        )

    @staticmethod
    def _raw_relation_count(results: list[Any]) -> int:
        return sum(
            len(result.relations) for result in results if isinstance(result, ReadResult)
        )

    def _emit_result_ready(self, result: ReadResult, started_at: float) -> None:
        profile_result = ReadResult(memories=[])
        search_result = result
        if result.memories:
            first = result.memories[0]
            if (
                first.id == self.ctx.end_user_id
                and _source_value(first.source) == MemorySource.EXTRACTED_ENTITY.value
            ):
                profile_result = ReadResult(memories=[first])
                search_result = ReadResult(
                    memories=result.memories[1:], relations=result.relations
                )
        items = project_result_items(profile_result, search_result, limit=5)
        self._emit_stage(
            "result_ready",
            {
                "duration_ms": max(
                    1, int(round((time.perf_counter() - started_at) * 1000))
                ),
                "total_count": len(result.memories),
                "shown_count": len(items),
                "items": items,
            },
        )

    def _elapsed_ms(self) -> int:
        return max(1, int(round((time.perf_counter() - self._run_started_at) * 1000)))

    def _ensure_run_started(self) -> None:
        if not self._run_started_at:
            self._run_started_at = time.perf_counter()


def _default_today() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d")


__all__ = [
    "DEFAULT_RETRIEVAL_LIMIT",
    "QUICK_SEARCH_ENTITY_LIMIT",
    "ReadPipeline",
]