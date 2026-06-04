import asyncio
import logging

from app.core.memory.enums import SearchStrategy, StorageType
from app.core.memory.models.service_models import MemorySearchResult
from app.core.memory.pipelines.base_pipeline import ModelClientMixin, DBRequiredPipeline
from app.core.memory.read_services.generate_engine.query_preprocessor import QueryPreprocessor
from app.core.memory.read_services.generate_engine.retrieval_summary import RetrievalSummaryProcessor
from app.core.memory.read_services.search_engine.content_search import Neo4jSearchService, RAGSearchService

logger = logging.getLogger(__name__)

CONVERSATION_WINDOW = 8


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
        memory_l0 = None
        if self.ctx.storage_type == StorageType.NEO4J:
            memory_l0 = await self._get_search_service(includes).memory_l0()

        query = QueryPreprocessor.process(query)
        match search_switch:
            case SearchStrategy.DEEP:
                res = await self._deep_read(query, history, limit, includes=includes, memory_l0=memory_l0)
            case SearchStrategy.NORMAL:
                res = await self._normal_read(query, history, limit, includes=includes, memory_l0=memory_l0)
            case SearchStrategy.QUICK:
                res = await self._quick_read(query, limit, includes)
                if memory_l0 is not None:
                    res.content_str = memory_l0.content + '\n' + res.content
                    res.memories.insert(0, memory_l0)
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
            memory_l0=None
    ) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        questions = await QueryPreprocessor.split(
            query,
            history,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        # 所有搜索任务扁平化并行执行
        all_tasks = []
        for question in questions:
            all_tasks.append(search_service.hybrid_search(question, limit))
            all_tasks.append(search_service.relation_search(question))

        all_results = list(await asyncio.gather(*all_tasks, return_exceptions=True))

        # 交错分区还原: [hybrid_0, relation_0, hybrid_1, relation_1, ...]
        hybrid_results = all_results[::2]
        relation_results = all_results[1::2]

        hybrid_search_res = _safe_merge_results(hybrid_results, "hybrid")
        relation_res = _safe_merge_results(relation_results, "relation")

        results = hybrid_search_res + relation_res

        # results = sum(query_results, start=MemorySearchResult(memories=[]))
        results.memories.sort(key=lambda x: x.score, reverse=True)
        results.content_str = await RetrievalSummaryProcessor.summary(
            query,
            results.content,
            memory_l0.content if memory_l0 else '',
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        return results

    async def _normal_read(
            self, query: str,
            history: list,
            limit: int,
            includes=None,
            memory_l0=None
    ) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        questions = await QueryPreprocessor.split(
            query,
            history,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        all_results = list(await asyncio.gather(*(
            search_service.hybrid_search(question, limit) for question in questions
        ), return_exceptions=True))
        results = _safe_merge_results(all_results, "normal")
        results.memories.sort(key=lambda x: x.score, reverse=True)
        results.content_str = await RetrievalSummaryProcessor.summary(
            query,
            results.content,
            memory_l0.content if memory_l0 else '',
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        return results

    async def _quick_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        return await search_service.hybrid_search(query, limit)
