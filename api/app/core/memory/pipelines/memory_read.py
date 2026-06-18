import asyncio
import logging

from app.core.memory.enums import SearchStrategy, StorageType
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
from app.db import get_db_context
from app.repositories.memory_short_repository import (
    ShortTermMemoryRepository,
)

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

    async def run(
            self,
            query: str,
            search_switch: SearchStrategy,
            history: list,
            limit: int = 10,
            includes=None
    ) -> MemorySearchResult:
        query = QueryPreprocessor.process(query)
        match search_switch:
            case SearchStrategy.DEEP:
                res = await self._deep_read(query, history, limit, includes=includes)
            case SearchStrategy.NORMAL:
                res = await self._normal_read(query, history, limit, includes=includes)
            case SearchStrategy.QUICK:
                return await self._quick_read(query, limit, includes)
            case SearchStrategy.EXPRESS:
                return await self._express_read(query, limit, includes)
            case SearchStrategy.CONV:
                return await self._conv_history()
            case SearchStrategy.META:
                return await self._user_meta()
            case _:
                raise RuntimeError("Unsupported search strategy")

        if search_switch in [SearchStrategy.DEEP, SearchStrategy.NORMAL] and not self.ctx.draft:
            self._save_short_term(query, search_switch, res)

        return res

    def _get_search_service(self, includes=None, need_embedder=True, need_llm=True):
        if self.ctx.storage_type == StorageType.NEO4J:
            return Neo4jSearchService(
                self.ctx,
                embedder=self._get_embedding_client() if need_embedder else None,
                llm=self._get_llm_client() if need_llm else None,
                includes=includes,
            )
        else:
            return RAGSearchService(self.ctx)

    def _get_llm_client(self):
        """懒加载 LLM client：首次调用借短连接查 model API key，后续复用缓存。"""
        if self._llm_client is None:
            with get_db_context() as db:
                self._llm_client = self.get_llm_client(db, self.ctx.memory_config.llm_model_id)
        return self._llm_client

    def _get_embedding_client(self):
        """懒加载 embedding client：首次调用借短连接查 model API key，后续复用缓存。"""
        if self._embedding_client is None:
            with get_db_context() as db:
                self._embedding_client = self.get_embedding_client(db, self.ctx.memory_config.embedding_model_id)
        return self._embedding_client

    async def _deep_read(
            self,
            query: str,
            history: list,
            limit: int,
            includes=None,
    ) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        memory_l0 = await self._user_meta()
        questions, memory_evidence = await QueryPreprocessor.split(
            query,
            history,
            memory_l0.content,
            self._get_llm_client()
        )
        if memory_evidence:
            memory_l0.content_str = str(memory_evidence)
            return memory_l0
        all_tasks = []
        for question in questions:
            all_tasks.append(_run_with_semaphore(search_service.hybrid_search(question, limit)))
            all_tasks.append(_run_with_semaphore(search_service.relation_search(question)))

        all_results = list(await asyncio.gather(*all_tasks, return_exceptions=True))

        hybrid_results = all_results[::2]
        relation_results = all_results[1::2]

        hybrid_search_res = _safe_merge_results(hybrid_results, "hybrid")
        relation_res = _safe_merge_results(relation_results, "relation")

        results = hybrid_search_res + relation_res

        results.memories.sort(key=lambda x: x.score, reverse=True)
        results.content_str = await RetrievalSummaryProcessor.summary(
            query,
            results.content,
            memory_l0.content if memory_l0 else '',
            self._get_llm_client()
        )

        return memory_l0 + results

    async def _normal_read(
            self, query: str,
            history: list,
            limit: int,
            includes=None,
    ) -> MemorySearchResult:
        search_service = self._get_search_service(includes)

        memory_l0 = await self._user_meta()
        questions, memory_evidence = await QueryPreprocessor.split(
            query,
            history,
            memory_l0.content,
            self._get_llm_client()
        )
        if memory_evidence:
            memory_l0.content_str = memory_evidence
            if memory_l0.memories:
                memory_l0.memories[0].query = query
            return memory_l0
        all_results = list(await asyncio.gather(*(
            _run_with_semaphore(search_service.hybrid_search(question, limit)) for question in questions
        ), return_exceptions=True))
        results = _safe_merge_results(all_results, "normal")
        results.memories.sort(key=lambda x: x.score, reverse=True)
        results.content_str = await RetrievalSummaryProcessor.summary(
            query,
            results.content,
            memory_l0.content if memory_l0 else '',
            self._get_llm_client()
        )
        return memory_l0 + results

    async def _express_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        """仅全文检索模式：不做 embedding、关系检索、query 拆分、摘要生成。"""
        meta_task = asyncio.ensure_future(self._user_meta())
        search_service = self._get_search_service(includes, need_embedder=False, need_llm=False)
        express_res = await search_service.keyword_search(query, limit)
        memory_l0 = await meta_task
        return memory_l0 + express_res

    async def _quick_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        meta_task = asyncio.ensure_future(self._user_meta())
        search_service = self._get_search_service(includes, need_llm=False)
        quick_res = await search_service.hybrid_search(query, limit)
        memory_l0 = await meta_task
        return memory_l0 + quick_res

    async def _conv_history(self) -> MemorySearchResult:
        service = HistorySearchService(self.ctx)
        return await service.run()

    async def _user_meta(self) -> MemorySearchResult:
        service = MetaSearchService(self.ctx)
        return await service.run()

    def _save_short_term(
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

            with get_db_context() as db:
                repo = ShortTermMemoryRepository(db)
                repo.upsert(
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
