import asyncio
import logging
import time

from app.core.memory.enums import Neo4jNodeType, SearchStrategy, StorageType
from app.core.memory.models.service_models import MemorySearchResult
from app.core.memory.pipelines.base_pipeline import BasePipeline, ModelClientMixin
from app.core.memory.read_services.generate_engine.query_preprocessor import QueryPreprocessor
from app.core.memory.read_services.generate_engine.retrieval_summary import RetrievalSummaryProcessor
from app.core.memory.read_services.search_engine.content_search import (
    Neo4jSearchService,
    RAGSearchService,
    HistorySearchService,
    MetaSearchService
)
from app.core.memory.retrieval_trace.stage_events import emit_memory_stage
from app.core.memory.retrieval_trace.stage_projection import (
    project_memory_items,
    project_profile_data,
    profile_has_content,
    project_relation_items,
    project_result_items,
)
from app.core.models import RedBearLLM
from app.db import get_async_db_context
from app.repositories.memory_short_repository import ShortTermMemoryRepository

logger = logging.getLogger(__name__)


async def _run_with_semaphore(coro):
    """直接执行协程（并发限制已关闭）。"""
    return await coro


def _safe_merge_results(results: list, label: str) -> MemorySearchResult:
    """合并搜索结果列表，跳过异常项并记录警告。"""
    merged = MemorySearchResult(memories=[])
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"[DeepRead] {label} search error (question #{i}): {result}")
        elif isinstance(result, MemorySearchResult):
            merged = merged + result
    return merged


class ReadPipeLine(ModelClientMixin, BasePipeline):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._embedding_client = None
        self._llm_client = None
        self._vision_llm_client = None
        self._audio_llm_client = None
        self._rerank_client = None
        self._run_started_at = 0.0

    async def run(
            self,
            query: str,
            search_switch: SearchStrategy,
            history: list,
            limit: int = 10,
            includes=None,
            skip_summary=False,
            enable_rerank: bool = False,
    ) -> MemorySearchResult:
        started_at = time.perf_counter()
        self._run_started_at = started_at
        query = QueryPreprocessor.process(query)
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

        if search_switch in [SearchStrategy.QUICK, SearchStrategy.EXPRESS, SearchStrategy.META]:
            await self._emit_result_ready(res, started_at)

        if search_switch in [SearchStrategy.DEEP, SearchStrategy.NORMAL] and not self.ctx.draft:
            await self._save_short_term(query, search_switch, res)

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
            "duration_ms": max(0, int(round((time.perf_counter() - started_at) * 1000))),
            "total_count": len(result.memories),
            "shown_count": len(items),
            "items": items,
        })

    def _elapsed_ms(self) -> int:
        return max(0, int(round((time.perf_counter() - self._run_started_at) * 1000)))

    def _ensure_run_started(self) -> None:
        if not self._run_started_at:
            self._run_started_at = time.perf_counter()

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
            await self._get_llm_client()
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

        hybrid_search_res = _safe_merge_results(hybrid_results, "hybrid")
        relation_res = _safe_merge_results(relation_results, "relation")
        hybrid_search_res.memories.sort(key=lambda item: item.score, reverse=True)
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
        if perceptual_memories:
            parse_tasks = []
            type_llm_cache: dict[int, RedBearLLM] = {}

            async def _get_llm_for_type(pt: int):
                if pt not in type_llm_cache:
                    type_llm_cache[pt] = await self._get_perceptual_llm_client(pt)
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
                    pt = int(mem.data.get("perceptual_type", 0))
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
                                    break

        results = hybrid_search_res + relation_res

        await self._emit_stage("results_merged", {
            "memory_count": len(results.memories),
            "relation_count": len(results.relations),
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
                await self._get_llm_client()
            )

        await self._emit_stage("context_prepared", {"memory_count": len(results.memories)})

        combined = memory_l0 + results
        items = project_result_items(memory_l0, results, limit=5)
        await self._emit_stage("result_ready", {
            "duration_ms": self._elapsed_ms() if hasattr(self, "_elapsed_ms") else 0,
            "total_count": len(combined.memories),
            "shown_count": len(items),
            "items": items,
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
            await self._get_llm_client()
        )
        await self._emit_stage("query_split", {
            "count": len(questions),
            "questions": [str(question)[:100] for question in questions[:5]],
        })
        all_results = list(await asyncio.gather(*(
            _run_with_semaphore(search_service.hybrid_search(question, limit)) for question in questions
        ), return_exceptions=True))
        results = _safe_merge_results(all_results, "normal")
        results.memories.sort(key=lambda x: x.score, reverse=True)
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
                await self._get_llm_client()
            )
        await self._emit_stage("context_prepared", {"memory_count": len(results.memories)})
        combined = memory_l0 + results
        items = project_result_items(memory_l0, results, limit=5)
        await self._emit_stage("result_ready", {
            "duration_ms": self._elapsed_ms() if hasattr(self, "_elapsed_ms") else 0,
            "total_count": len(combined.memories),
            "shown_count": len(items),
            "items": items,
        })
        return combined

    async def _express_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        """仅全文检索模式：不做 embedding、关系检索、query 拆分、摘要生成。"""
        meta_task = asyncio.ensure_future(self._user_meta())
        search_service = await self._get_search_service(includes, need_embedder=False, need_llm=False)
        express_res = await search_service.keyword_search(query, limit)
        memory_l0 = await meta_task
        return memory_l0 + express_res

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
        return memory_l0 + quick_res

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
