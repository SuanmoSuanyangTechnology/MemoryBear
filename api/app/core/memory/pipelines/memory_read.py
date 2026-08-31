from __future__ import annotations

import asyncio
import logging
import time
import uuid

from app.core.memory.enums import Neo4jNodeType, SearchStrategy, StorageType
from app.core.memory.models.service_models import MemorySearchResult
from app.core.memory.pipelines.base_pipeline import BasePipeline, ModelClientMixin
from app.core.memory.alerts import enqueue_memory_retrieval_alert_safely
from app.core.memory.exceptions import (
    MemoryRetrievalBusinessError,
    MemoryRetrievalImpact,
)
from app.core.memory.read_services.generate_engine.query_preprocessor import QueryPreprocessor
from app.core.memory.read_services.generate_engine.retrieval_summary import RetrievalSummaryProcessor
from app.core.memory.read_services.search_engine.content_search import (
    Neo4jSearchService,
    RAGSearchService,
    HistorySearchService,
    MetaSearchService
)
from app.core.memory.retrieval_trace.stage_events import emit_memory_stage
from app.core.memory.retrieval_trace.models import RetrievalExecutionTrace
from app.core.memory.retrieval_trace.stage_projection import (
    project_memory_items,
    project_profile_data,
    profile_has_content,
    project_relation_items,
    project_result_items,
)
from app.core.models import RedBearLLM
from app.core.utils.datetime_utils import utcnow, utcnow_naive
from app.db import get_async_db_context
from app.repositories.memory_short_repository import ShortTermMemoryRepository
from app.schemas.memory_retrieval_display_schema import (
    RETRIEVE_SEARCH_MODES,
    RetrieveDisplayTask,
)
from app.services.memory_retrieval_display_queue import MemoryRetrievalDisplayQueue
from app.services.memory_retrieval_display_service import (
    build_retrieve_snapshot,
    clean_query_for_display,
)

logger = logging.getLogger(__name__)


async def _run_with_semaphore(coro):
    """直接执行协程（并发限制已关闭）。"""
    return await coro


def _safe_merge_results(
    results: list,
    label: str,
    *,
    on_error,
) -> MemorySearchResult:
    """合并搜索结果列表，跳过异常项并记录警告。"""
    merged = MemorySearchResult(memories=[])
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            if isinstance(result, MemoryRetrievalBusinessError):
                on_error(result.with_impact(MemoryRetrievalImpact.INCOMPLETE))
            logger.warning(f"[DeepRead] {label} search error (question #{i}): {result}")
        elif isinstance(result, MemorySearchResult):
            merged = merged + result
    return merged


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


def _merge_execution_traces(results: list, *, merged_count: int) -> RetrievalExecutionTrace:
    """聚合多 Query 的执行状态，不参与结果合并和排序。"""
    traces = [
        result.execution_trace
        for result in results
        if isinstance(result, MemorySearchResult) and result.execution_trace is not None
    ]
    reasons = [reason for trace in traces for reason in trace.degraded_reasons]
    has_failed_subquery = any(isinstance(result, Exception) for result in results)
    if has_failed_subquery:
        reasons.append("subquery_failed")
    keyword_status = _aggregate_branch_status([trace.keyword_status for trace in traces])
    semantic_status = _aggregate_branch_status([trace.semantic_status for trace in traces])
    if has_failed_subquery:
        keyword_status = "failed" if keyword_status == "skipped" else "degraded"
        semantic_status = "failed" if semantic_status == "skipped" else "degraded"
    return RetrievalExecutionTrace(
        keyword_status=keyword_status,
        semantic_status=semantic_status,
        rerank_status=_aggregate_branch_status([trace.rerank_status for trace in traces]),
        keyword_hit_count=sum(trace.keyword_hit_count for trace in traces),
        semantic_hit_count=sum(trace.semantic_hit_count for trace in traces),
        raw_hit_count=sum(trace.raw_hit_count for trace in traces),
        merged_count=merged_count,
        degraded_reasons=list(dict.fromkeys(reasons)),
    )


def _attach_matched_queries(final_result: MemorySearchResult, source_results: list) -> None:
    """给最终保留候选补充命中 Query，不改变原有去重和合并语义。"""
    query_map: dict[tuple[str, str], list[str]] = {}
    for result in source_results:
        if not isinstance(result, MemorySearchResult):
            continue
        for memory in result.memories:
            if memory.query:
                key = (memory.source.value, str(memory.id))
                query_map.setdefault(key, []).append(str(memory.query))
    for memory in final_result.memories:
        trace = memory.retrieval_trace
        if trace is None:
            continue
        key = (memory.source.value, str(memory.id))
        trace.matched_queries = list(dict.fromkeys(query_map.get(key, trace.matched_queries)))


class ReadPipeLine(ModelClientMixin, BasePipeline):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._embedding_client = None
        self._llm_client = None
        self._vision_llm_client = None
        self._audio_llm_client = None
        self._rerank_client = None
        self._run_started_at = 0.0
        self._notification_error: MemoryRetrievalBusinessError | None = None
        self._retrieval_operation_id = ""

    def _record_retrieval_error(self, error: MemoryRetrievalBusinessError) -> None:
        if self._notification_error is None:
            self._notification_error = error

    async def _enqueue_retrieval_alert(self, error: MemoryRetrievalBusinessError) -> None:
        tenant_id = str(getattr(self.ctx.memory_config, "tenant_id", "") or "")
        workspace_id = str(getattr(self.ctx.memory_config, "workspace_id", "") or "")
        try:
            await enqueue_memory_retrieval_alert_safely(
                error,
                operation_id=self._retrieval_operation_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                end_user_id=str(self.ctx.end_user_id),
            )
        except Exception:
            # 双层保护：即使安全上报封装自身回归，也不能改变检索结果或原异常。
            logger.exception(
                "[ReadPipeLine] retrieval alert enqueue escaped safe wrapper; ignored "
                "operation_id=%s",
                self._retrieval_operation_id,
            )

    async def run(
            self,
            query: str,
            search_switch: SearchStrategy,
            history: list,
            limit: int = 10,
            includes=None,
            skip_summary=False,
            enable_rerank: bool = False,
            record_display: bool = False,
            retrieval_operation_id: str | None = None,
    ) -> MemorySearchResult:
        started_at = time.perf_counter()
        self._run_started_at = started_at
        self._retrieval_operation_id = retrieval_operation_id or uuid.uuid4().hex
        self._notification_error = None
        original_query = query
        query = QueryPreprocessor.process(query)
        if search_switch in {
            SearchStrategy.DEEP,
            SearchStrategy.NORMAL,
            SearchStrategy.QUICK,
            SearchStrategy.EXPRESS,
        }:
            await self._emit_stage("query_preprocessed", {
                "original_query": original_query[:2000],
                "processed_query": query[:2000],
                "will_split": search_switch in {SearchStrategy.DEEP, SearchStrategy.NORMAL},
            })
        # 展示用主问题必须在 deep/normal 的问题拆分之前固定下来
        display_query = clean_query_for_display(query)

        try:
            match search_switch:
                case SearchStrategy.DEEP:
                    res = await self._deep_read(query, history, limit,
                                                includes=includes, skip_summary=skip_summary,
                                                enable_rerank=enable_rerank)
                case SearchStrategy.NORMAL:
                    res = await self._normal_read(query, history, limit,
                                                  includes=includes, skip_summary=skip_summary,
                                                  enable_rerank=enable_rerank)
                case SearchStrategy.QUICK:
                    res = await self._quick_read(query, limit, includes,
                                                 enable_rerank=enable_rerank)
                case SearchStrategy.EXPRESS:
                    res = await self._express_read(query, limit, includes)
                case SearchStrategy.RECENT:
                    return await self._conv_history()
                case SearchStrategy.META:
                    res = await self._user_meta()
                case _:
                    raise RuntimeError("Unsupported search strategy")
        except Exception:
            # 只做旁路通知，随后原样抛出原检索异常，不改变异常类型和调用栈。
            if self._notification_error is not None:
                await self._enqueue_retrieval_alert(self._notification_error)
            raise

        if search_switch in [SearchStrategy.QUICK, SearchStrategy.EXPRESS, SearchStrategy.META]:
            await self._emit_result_ready(res, started_at)

        if search_switch in [SearchStrategy.DEEP, SearchStrategy.NORMAL] and not self.ctx.draft:
            await self._save_short_term(query, search_switch, res)

        if record_display:
            self._dispatch_display_record(display_query, search_switch, res)

        if self._notification_error is not None:
            await self._enqueue_retrieval_alert(self._notification_error)

        if res.execution_trace is None:
            res.execution_trace = RetrievalExecutionTrace()
        res.execution_trace.original_query = original_query
        res.execution_trace.processed_query = query
        res.execution_trace.search_switch = search_switch.value
        res.execution_trace.backend = self.ctx.storage_type.value
        res.execution_trace.limit = limit

        return res

    async def _emit_stage(self, stage: str, data: dict) -> None:
        await emit_memory_stage(stage, data)

    @staticmethod
    def _raw_memory_count(results: list) -> int:
        return sum(len(result.memories) for result in results if isinstance(result, MemorySearchResult))

    @staticmethod
    def _raw_relation_count(results: list) -> int:
        return sum(len(result.relations) for result in results if isinstance(result, MemorySearchResult))

    async def _emit_result_ready(self, result: MemorySearchResult, started_at: float) -> None:
        profile_result = MemorySearchResult(memories=[])
        search_result = result
        if result.memories:
            first = result.memories[0]
            # 非深度策略返回扁平列表，只有首项满足用户实体约定时才按画像投影。
            if first.id == getattr(self.ctx, "end_user_id", None) and first.source == Neo4jNodeType.EXTRACTEDENTITY:
                profile_result = MemorySearchResult(memories=[first])
                search_result = MemorySearchResult(memories=result.memories[1:], relations=result.relations)
        items = project_result_items(profile_result, search_result, limit=5)
        await self._emit_stage("result_ready", {
            "duration_ms": max(1, int(round((time.perf_counter() - started_at) * 1000))),
            "total_count": len(result.memories),
            "shown_count": len(items),
            "items": items,
        })

    def _elapsed_ms(self) -> int:
        return max(1, int(round((time.perf_counter() - self._run_started_at) * 1000)))

    def _ensure_run_started(self) -> None:
        if not self._run_started_at:
            self._run_started_at = time.perf_counter()
    def _dispatch_display_record(
            self,
            display_query: str,
            search_switch: SearchStrategy,
            result: MemorySearchResult,
    ) -> None:
        """聚合读取展示快照并非阻塞投递，任何异常都不影响检索返回。"""
        try:
            search_mode = search_switch.name.lower()
            if search_mode not in RETRIEVE_SEARCH_MODES:
                logger.debug(
                    f"[ReadPipeLine] 检索方式 {search_mode} 不在读取展示白名单内，跳过投递"
                )
                return

            try:
                end_user_uuid = uuid.UUID(str(self.ctx.end_user_id))
            except (ValueError, AttributeError, TypeError):
                logger.warning(
                    f"[ReadPipeLine] end_user_id 不是合法 UUID，跳过读取展示投递: "
                    f"{self.ctx.end_user_id}"
                )
                return

            snapshot = build_retrieve_snapshot(
                result=result,
                query=display_query,
                language=self.ctx.language,
            )
            if not snapshot:
                logger.debug(
                    "[ReadPipeLine] 本次检索没有可展示的 Summary/Entity，跳过投递"
                )
                return

            MemoryRetrievalDisplayQueue.enqueue_nowait(
                RetrieveDisplayTask(
                    id=uuid.uuid4(),
                    operation_id=uuid.uuid4(),
                    end_user_id=end_user_uuid,
                    search_mode=search_mode,
                    query=snapshot["query"],
                    content=snapshot["content"],
                    occurred_at=utcnow_naive(),
                )
            )
        except Exception as e:
            logger.warning(
                f"[ReadPipeLine] 读取展示投递失败（不影响主流程）: {e}",
                exc_info=True,
            )

    async def _get_search_service(
            self,
            includes=None,
            need_embedder=True,
            need_llm=True,
            enable_rerank: bool = False
    ):
        if self.ctx.storage_type == StorageType.NEO4J:
            if need_embedder and need_llm:
                embedder, llm = await asyncio.gather(
                    self._get_embedding_client(),
                    self._get_llm_client(),
                )
            else:
                embedder = (await self._get_embedding_client()) if need_embedder else None
                llm = (await self._get_llm_client()) if need_llm else None

            reranker = None
            if enable_rerank:
                reranker = await self._get_rerank_client()
                if reranker is None:
                    logger.warning(
                        "[ReadPipeLine] enable_rerank=True but rerank_model_id not configured, "
                        "falling back to score fusion"
                    )

            return Neo4jSearchService(
                self.ctx,
                embedder=embedder,
                llm=llm,
                reranker=reranker,
                includes=includes,
                on_error=self._record_retrieval_error,
            )
        else:
            return RAGSearchService(self.ctx)

    async def _get_llm_client(self):
        """懒加载 LLM client：首次调用借短连接查 model API key，后续复用缓存。"""
        if self._llm_client is None:
            async with get_async_db_context() as db:
                self._llm_client = await self.get_llm_client_async(
                    db,
                    self.ctx.memory_config.llm_model_id,
                    tenant_id=self.ctx.memory_config.tenant_id
                )
        return self._llm_client

    async def _get_embedding_client(self):
        """懒加载 embedding client：首次调用借短连接查 model API key，后续复用缓存。"""
        if self._embedding_client is None:
            async with get_async_db_context() as db:
                self._embedding_client = await self.get_embedding_client_async(
                    db,
                    self.ctx.memory_config.embedding_model_id,
                    tenant_id=self.ctx.memory_config.tenant_id
                )
        return self._embedding_client

    async def _get_perceptual_llm_client(self, perceptual_type: int):
        cfg = self.ctx.memory_config
        if perceptual_type == 1:  # VISION
            if not cfg.vision_model_id:
                return None
            if self._vision_llm_client is None:
                async with get_async_db_context() as db:
                    self._vision_llm_client = await self.get_llm_client_async(
                        db, cfg.vision_model_id, tenant_id=cfg.tenant_id,
                    )
            return self._vision_llm_client
        elif perceptual_type == 2:  # AUDIO
            if not cfg.audio_model_id:
                return None
            if self._audio_llm_client is None:
                async with get_async_db_context() as db:
                    self._audio_llm_client = await self.get_llm_client_async(
                        db, cfg.audio_model_id, tenant_id=cfg.tenant_id,
                    )
            return self._audio_llm_client
        elif perceptual_type == 3:  # TEXT
            return await self._get_llm_client()
        else:
            return None

    async def _get_rerank_client(self):
        """懒加载 rerank client，仅在 rerank_model_id 已配置时可用。

        Returns:
            RedBearRerank | None: 如果 rerank_model_id 未配置则返回 None
        """
        cfg = self.ctx.memory_config
        if cfg.rerank_model_id is None:
            return None
        if self._rerank_client is None:
            async with get_async_db_context() as db:
                self._rerank_client = await self.get_rerank_client_async(
                    db,
                    cfg.rerank_model_id,
                    tenant_id=cfg.tenant_id
                )
        return self._rerank_client

    async def _deep_read(
            self,
            query: str,
            history: list,
            limit: int,
            includes: list,
            skip_summary=False,
            enable_rerank: bool = False,
    ) -> MemorySearchResult:
        self._ensure_run_started()
        search_service = await self._get_search_service(includes, enable_rerank=enable_rerank)
        memory_l0 = await self._user_meta()
        profile = project_profile_data(memory_l0)
        await self._emit_stage("profile_loaded", {
            "has_profile": profile_has_content(profile),
            "profile": profile,
        })
        questions = await QueryPreprocessor.split(
            query,
            history,
            memory_l0.content,
            await self._get_llm_client(),
            on_error=self._record_retrieval_error,
        )
        await self._emit_stage("query_split", {
            "count": len(questions),
            "questions": [str(question)[:100] for question in questions[:5]],
        })
        all_tasks = []
        for question in questions:
            all_tasks.append(_run_with_semaphore(search_service.hybrid_search(question, limit)))
            all_tasks.append(_run_with_semaphore(search_service.relation_search(question)))

        all_results = list(await asyncio.gather(*all_tasks, return_exceptions=True))

        hybrid_results = all_results[::2]
        relation_results = all_results[1::2]

        hybrid_search_res = _safe_merge_results(
            hybrid_results,
            "hybrid",
            on_error=self._record_retrieval_error,
        )
        relation_res = _safe_merge_results(
            relation_results,
            "relation",
            on_error=self._record_retrieval_error,
        )
        hybrid_search_res.memories.sort(key=lambda item: item.score, reverse=True)
        _attach_matched_queries(hybrid_search_res, hybrid_results)
        hybrid_execution_trace = _merge_execution_traces(
            hybrid_results,
            merged_count=len(hybrid_search_res.memories),
        )
        relation_res.relations = list(relation_res.relations)
        await self._emit_stage("hybrid_searched", {
            "hit_count": self._raw_memory_count(hybrid_results),
            "memory_count": len(hybrid_search_res.memories),
            "shown_count": min(3, len(hybrid_search_res.memories)),
            "items": project_memory_items(hybrid_search_res.memories, limit=3),
        })
        await self._emit_stage("relation_searched", {
            "hit_count": self._raw_relation_count(relation_results),
            "relation_count": len(relation_res.relations),
            "shown_count": min(3, len(relation_res.relations)),
            "items": project_relation_items(relation_res.relations, limit=3),
        })

        perceptual_memories = [
            m for m in hybrid_search_res.memories
            if m.source == Neo4jNodeType.PERCEPTUAL
        ]
        # 阶段事件展示的是命中的感知记忆总数，不能因解析失败或跳过解析而减少。
        perceptual_memory_count = len(perceptual_memories)
        if perceptual_memories:
            parse_tasks = []
            # 同一感知类型复用模型客户端；初始化失败时记为 None，统一降级使用原 summary。
            type_llm_cache: dict[int, RedBearLLM | None] = {}

            async def _get_llm_for_type(pt: int):
                if pt not in type_llm_cache:
                    try:
                        type_llm_cache[pt] = await self._get_perceptual_llm_client(pt)
                    except Exception:
                        logger.warning(
                            "[DeepRead] Unable to initialize perceptual model for type %s; using stored summary",
                            pt,
                            exc_info=True,
                        )
                        type_llm_cache[pt] = None
                return type_llm_cache[pt]

            for i, question in enumerate(questions):
                if i >= len(hybrid_results) or isinstance(hybrid_results[i], Exception):
                    continue
                sub_percep = [
                    m for m in hybrid_results[i].memories
                    if m.source == Neo4jNodeType.PERCEPTUAL
                ]
                if not sub_percep:
                    continue

                by_type: dict[int, list] = {}
                for mem in sub_percep:
                    # 源数据可能是整数或数字字符串，其他格式不参与感知解析。
                    raw_type = mem.data.get("perceptual_type", 0)
                    if isinstance(raw_type, int) and not isinstance(raw_type, bool):
                        pt = raw_type
                    elif isinstance(raw_type, str) and raw_type.strip().isdigit():
                        pt = int(raw_type.strip())
                    else:
                        logger.warning(
                            "[DeepRead] Invalid perceptual_type %r for memory %s; using stored summary",
                            raw_type,
                            mem.id,
                        )
                        continue
                    # 公开协议只允许感知类型 1、2、3，其他值保留原 summary 并对外投影为 null。
                    if pt not in {1, 2, 3}:
                        logger.warning(
                            "[DeepRead] Unsupported perceptual_type %r for memory %s; using stored summary",
                            raw_type,
                            mem.id,
                        )
                        continue
                    file_type = str(mem.data.get("file_type") or "").strip().lower()
                    # VISION 同时包含图片和视频，本期仅图片和文档执行面向查询的内容解析。
                    if (pt, file_type) not in {(1, "image"), (3, "document")}:
                        continue
                    if pt not in by_type:
                        by_type[pt] = []
                    by_type[pt].append(mem)

                for pt, mems in by_type.items():
                    llm = await _get_llm_for_type(pt)
                    if llm is None:
                        continue
                    parse_tasks.append(
                        search_service.resolve_perceptual_content(
                            question, mems, llm
                        )
                    )

            if parse_tasks:
                parse_results = await asyncio.gather(*parse_tasks, return_exceptions=True)
                for parse_res in parse_results:
                    if not isinstance(parse_res, Exception):
                        for mem in parse_res:
                            for existing in hybrid_search_res.memories:
                                if existing.id == mem.id:
                                    existing.content = mem.content
                                    # 公开纯文本独立保存，避免把内部 history-file-input 标签发给前端。
                                    display_content = mem.data.get("_perceptual_display_content")
                                    if display_content:
                                        existing.data["_perceptual_display_content"] = display_content
                                    break

        results = hybrid_search_res + relation_res
        merged_memory_count = len(results.memories)
        merged_relation_count = len(results.relations)
        results.memories.sort(key=lambda x: x.score, reverse=True)
        # 两个完成阶段复用同一份最终前 5 投影，保证条目、排名和分数完全一致。
        final_items = project_result_items(memory_l0, results, limit=5)
        perceptual_items = [
            item
            for item in final_items
            if item.get("memory_type") == "file" and item.get("source") == "Perceptual"
        ]

        if perceptual_memory_count > 0:
            await self._emit_stage("perceptual_processed", {
                "memory_count": perceptual_memory_count,
                "shown_count": len(perceptual_items),
                "items": perceptual_items,
            })

        await self._emit_stage("results_merged", {
            "memory_count": merged_memory_count,
            "relation_count": merged_relation_count,
        })

        await self._emit_stage("results_ranked", {
            "count": len(results.memories),
            "order": "score_desc",
        })
        if not skip_summary:
            results.content_str = await RetrievalSummaryProcessor.summary(
                query,
                results.content,
                memory_l0.content if memory_l0 else '',
                await self._get_llm_client(),
                on_error=self._record_retrieval_error,
            )

        await self._emit_stage("context_prepared", {"memory_count": len(results.memories)})

        combined = memory_l0 + results
        combined.execution_trace = hybrid_execution_trace
        await self._emit_stage("result_ready", {
            "duration_ms": self._elapsed_ms() if hasattr(self, "_elapsed_ms") else 1,
            "total_count": len(combined.memories),
            "shown_count": len(final_items),
            "items": final_items,
        })

        return combined

    async def _normal_read(
            self, query: str,
            history: list,
            limit: int,
            includes=None,
            skip_summary=False,
            enable_rerank: bool = False,
    ) -> MemorySearchResult:
        self._ensure_run_started()
        search_service = await self._get_search_service(includes, enable_rerank=enable_rerank)

        memory_l0 = await self._user_meta()
        profile = project_profile_data(memory_l0)
        await self._emit_stage("profile_loaded", {
            "has_profile": profile_has_content(profile),
            "profile": profile,
        })
        questions = await QueryPreprocessor.split(
            query,
            history,
            memory_l0.content,
            await self._get_llm_client(),
            on_error=self._record_retrieval_error,
        )
        await self._emit_stage("query_split", {
            "count": len(questions),
            "questions": [str(question)[:100] for question in questions[:5]],
        })
        all_results = list(await asyncio.gather(*(
            _run_with_semaphore(search_service.hybrid_search(question, limit)) for question in questions
        ), return_exceptions=True))
        results = _safe_merge_results(
            all_results,
            "normal",
            on_error=self._record_retrieval_error,
        )
        results.memories.sort(key=lambda x: x.score, reverse=True)
        _attach_matched_queries(results, all_results)
        results.execution_trace = _merge_execution_traces(
            all_results,
            merged_count=len(results.memories),
        )
        await self._emit_stage("hybrid_searched", {
            "hit_count": self._raw_memory_count(all_results),
            "memory_count": len(results.memories),
            "shown_count": min(3, len(results.memories)),
            "items": project_memory_items(results.memories, limit=3),
        })
        await self._emit_stage("results_merged", {
            "memory_count": len(results.memories),
            "relation_count": 0,
        })
        results.memories.sort(key=lambda x: x.score, reverse=True)
        await self._emit_stage("results_ranked", {
            "count": len(results.memories),
            "order": "score_desc",
        })
        if not skip_summary:
            results.content_str = await RetrievalSummaryProcessor.summary(
                query,
                results.content,
                memory_l0.content if memory_l0 else '',
                await self._get_llm_client(),
                on_error=self._record_retrieval_error,
            )
        await self._emit_stage("context_prepared", {"memory_count": len(results.memories)})
        combined = memory_l0 + results
        combined.execution_trace = results.execution_trace
        items = project_result_items(memory_l0, results, limit=5)
        await self._emit_stage("result_ready", {
            "duration_ms": self._elapsed_ms() if hasattr(self, "_elapsed_ms") else 1,
            "total_count": len(combined.memories),
            "shown_count": len(items),
            "items": items,
        })
        return combined

    async def _express_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        """仅全文检索模式：不做 embedding、关系检索、query 拆分、摘要生成。"""
        if includes is None:
            includes = [
                Neo4jNodeType.CHUNK,
                Neo4jNodeType.STATEMENT,
                Neo4jNodeType.EXTRACTEDENTITY,
                Neo4jNodeType.DIALOGUE,
            ]
        meta_task = asyncio.ensure_future(self._user_meta())
        search_service = await self._get_search_service(includes, need_embedder=False, need_llm=False)
        express_res = await search_service.keyword_search(query, limit)
        memory_l0 = await meta_task
        profile = project_profile_data(memory_l0)
        await self._emit_stage("profile_loaded", {
            "has_profile": profile_has_content(profile),
            "profile": profile,
        })
        combined = memory_l0 + express_res
        combined.execution_trace = express_res.execution_trace
        return combined

    async def _quick_read(
            self,
            query: str,
            limit: int,
            includes=None,
            enable_rerank: bool = False
    ) -> MemorySearchResult:
        meta_task = asyncio.ensure_future(self._user_meta())
        search_service = await self._get_search_service(
            includes,
            need_llm=False,
            enable_rerank=enable_rerank
        )
        quick_res = await search_service.hybrid_search(query, limit)
        memory_l0 = await meta_task
        profile = project_profile_data(memory_l0)
        await self._emit_stage("profile_loaded", {
            "has_profile": profile_has_content(profile),
            "profile": profile,
        })
        combined = memory_l0 + quick_res
        combined.execution_trace = quick_res.execution_trace
        return combined

    async def _conv_history(self) -> MemorySearchResult:
        service = HistorySearchService(self.ctx)
        return await service.run()

    async def _user_meta(self) -> MemorySearchResult:
        service = MetaSearchService(self.ctx)
        return await service.run()

    async def _save_short_term(
            self,
            query: str,
            search_switch: SearchStrategy,
            result: MemorySearchResult,
    ) -> None:
        """将本次检索结果写入 memory_short_term 表。

        仅保存有效的检索结果（summary 不为空且非"信息不足"），
        失败不中断主流程。

        Args:
            query: 用户原始问题
            search_switch: 检索策略
            result: 检索结果（含 memories 和 content_str）
        """
        try:
            aimessages = result.content
            if not aimessages or "信息不足" in aimessages:
                logger.debug(
                    f"[ReadPipeLine] 跳过 short_term 写入: "
                    f"summary 为空或信息不足, end_user_id={self.ctx.end_user_id}"
                )
                return

            query_groups: dict[str, list[str]] = {}
            for memory in result.memories:
                if memory.content:
                    mem_query = memory.query or query
                    if mem_query not in query_groups:
                        query_groups[mem_query] = []
                    if memory.content not in query_groups[mem_query]:
                        query_groups[mem_query].append(memory.content)

            retrieved_content = [
                {q: contents} for q, contents in query_groups.items()
            ]

            async with get_async_db_context() as db:
                await ShortTermMemoryRepository.upsert_async(
                    db=db,
                    end_user_id=self.ctx.end_user_id,
                    messages=query,
                    aimessages=aimessages,
                    retrieved_content=retrieved_content,
                    search_switch=search_switch.value,
                )

            logger.info(
                f"[ReadPipeLine] short_term 写入成功: "
                f"end_user_id={self.ctx.end_user_id}, "
                f"queries={len(retrieved_content)}, "
                f"memories={len(result.memories)}"
            )
        except Exception as e:
            logger.warning(
                f"[ReadPipeLine] short_term 写入失败（不影响主流程）: {e}",
                exc_info=True,
            )
