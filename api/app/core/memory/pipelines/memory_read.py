import asyncio
import logging

from app.core.memory.enums import SearchStrategy, StorageType
from app.core.memory.models.service_models import MemorySearchResult
from app.core.memory.pipelines.base_pipeline import ModelClientMixin, DBRequiredPipeline
from app.core.memory.read_services.generate_engine.query_preprocessor import QueryPreprocessor
from app.core.memory.read_services.generate_engine.retrieval_summary import RetrievalSummaryProcessor
from app.core.memory.read_services.search_engine.content_search import Neo4jSearchService, RAGSearchService

logger = logging.getLogger(__name__)


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
            case _:
                raise RuntimeError("Unsupported search strategy")

        # if memory_l0 is not None:
        #     res.content_str = memory_l0.content + '\n' + res.content
        #     res.memories.insert(0, memory_l0)
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

    async def _deep_read(self, query: str, history: list, limit: int, includes=None, memory_l0=None) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        questions = await QueryPreprocessor.split(
            query,
            history,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        query_results = []
        for question in questions:
            hybrid_task = search_service.hybrid_search(question, limit)
            relation_task = search_service.relation_search(question)
            hybrid_search_results, relation_results = await asyncio.gather(
                hybrid_task, relation_task, return_exceptions=True
            )

            if isinstance(hybrid_search_results, Exception):
                logger.warning(f"[DeepRead] hybrid search error: {hybrid_search_results}")
                hybrid_search_results = MemorySearchResult(memories=[])

            if isinstance(relation_results, Exception):
                logger.warning(f"[DeepRead] relation search error: {relation_results}")
                relation_results = MemorySearchResult(memories=[])

            merged = hybrid_search_results + relation_results
            query_results.append(merged)
        results = sum(query_results, start=MemorySearchResult(memories=[]))
        results.memories.sort(key=lambda x: x.score, reverse=True)
        results.content_str = await RetrievalSummaryProcessor.summary(
            query,
            results.content,
            memory_l0.content,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        return results

    async def _normal_read(self, query: str, history: list, limit: int, includes=None, memory_l0=None) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        questions = await QueryPreprocessor.split(
            query,
            history,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        query_results = []
        for question in questions:
            search_results = await search_service.hybrid_search(question, limit)
            query_results.append(search_results)
        results = sum(query_results, start=MemorySearchResult(memories=[]))
        results.memories.sort(key=lambda x: x.score, reverse=True)
        results.content_str = await RetrievalSummaryProcessor.summary(
            query,
            results.content,
            memory_l0.content,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        return results

    async def _quick_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        return await search_service.hybrid_search(query, limit)
