import asyncio
import logging

from app.core.memory.enums import SearchStrategy, StorageType
from app.core.memory.models.service_models import MemorySearchResult
from app.core.memory.pipelines.base_pipeline import ModelClientMixin, DBRequiredPipeline
from app.core.memory.read_services.generate_engine.query_preprocessor import QueryPreprocessor
from app.core.memory.read_services.generate_engine.retrieval_summary import RetrievalSummaryProcessor
from app.core.memory.read_services.search_engine.content_search import (
    Neo4jSearchService,
    RAGSearchService,
    HistorySearchService,
    MetaSearchService
)

logger = logging.getLogger(__name__)

_MAX_SEARCH_CONCURRENCY = 3
_search_semaphore = asyncio.Semaphore(_MAX_SEARCH_CONCURRENCY)


async def _run_with_semaphore(coro):
    """在信号量控制下执行协程，限制并发搜索数量。"""
    async with _search_semaphore:
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


class ReadPipeLine(ModelClientMixin, DBRequiredPipeline):
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
            case SearchStrategy.CONV:
                return await self._conv_history()
            case SearchStrategy.META:
                return await self._user_meta()
            case _:
                raise RuntimeError("Unsupported search strategy")

        return res

    def _get_search_service(self, includes=None):
        if self.ctx.storage_type == StorageType.NEO4J:
            return Neo4jSearchService(
                self.ctx,
                self.get_embedding_client(self.db, self.ctx.memory_config.embedding_model_id),
                # self.get_rerank_client(self.db, self.ctx.memory_config.rerank_model_id),
                self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id),
                includes=includes,
            )
        else:
            return RAGSearchService(
                self.ctx,
                self.db
            )

    async def _deep_read(
            self,
            query: str,
            history: list,
            limit: int,
            includes=None,
    ) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        meta_task = asyncio.ensure_future(self._user_meta())
        questions = await QueryPreprocessor.split(
            query,
            history,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        memory_l0 = await meta_task
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
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )

        return memory_l0 + results

    async def _normal_read(
            self, query: str,
            history: list,
            limit: int,
            includes=None,
    ) -> MemorySearchResult:
        search_service = self._get_search_service(includes)

        meta_task = asyncio.ensure_future(self._user_meta())
        questions = await QueryPreprocessor.split(
            query,
            history,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        memory_l0 = await meta_task
        all_results = list(await asyncio.gather(*(
            _run_with_semaphore(search_service.hybrid_search(question, limit)) for question in questions
        ), return_exceptions=True))
        results = _safe_merge_results(all_results, "normal")
        results.memories.sort(key=lambda x: x.score, reverse=True)
        results.content_str = await RetrievalSummaryProcessor.summary(
            query,
            results.content,
            memory_l0.content if memory_l0 else '',
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        return memory_l0 + results

    async def _quick_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        meta_task = asyncio.ensure_future(self._user_meta())
        search_service = self._get_search_service(includes)
        quick_res = await search_service.hybrid_search(query, limit)
        memory_l0 = await meta_task
        return memory_l0 + quick_res

    async def _conv_history(self) -> MemorySearchResult:
        service = HistorySearchService(self.ctx, self.db)
        convs = await service.run()
        return convs

    async def _user_meta(self) -> MemorySearchResult:
        service = MetaSearchService(self.ctx, self.db)
        user_meta = await service.run()
        return user_meta
