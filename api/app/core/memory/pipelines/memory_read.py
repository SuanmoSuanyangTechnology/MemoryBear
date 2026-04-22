from app.core.memory.enums import SearchStrategy, StorageType
from app.core.memory.models.service_models import MemorySearchResult
from app.core.memory.pipelines.base_pipeline import ModelClientMixin, DBRequiredPipeline
from core.memory.read_services.search_engine.content_search import Neo4jSearchService, RAGSearchService
from core.memory.read_services.generate_engine.query_preprocessor import QueryPreprocessor


class ReadPipeLine(ModelClientMixin, DBRequiredPipeline):
    async def run(
            self,
            query: str,
            search_switch: SearchStrategy,
            limit: int = 10,
            includes=None
    ) -> MemorySearchResult:
        query = QueryPreprocessor.process(query)
        match search_switch:
            case SearchStrategy.DEEP:
                return await self._deep_read(query, limit, includes)
            case SearchStrategy.NORMAL:
                return await self._normal_read(query, limit, includes)
            case SearchStrategy.QUICK:
                return await self._quick_read(query, limit, includes)
            case _:
                raise RuntimeError("Unsupported search strategy")

    def _get_search_service(self, includes=None):
        if self.ctx.storage_type == StorageType.NEO4J:
            return Neo4jSearchService(
                self.ctx,
                self.get_embedding_client(self.db, self.ctx.memory_config.embedding_model_id),
                includes=includes,
            )
        else:
            return RAGSearchService(
                self.ctx,
                self.db
            )

    async def _deep_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        questions = await QueryPreprocessor.split(
            query,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        query_results = []
        for question in questions:
            search_results = await search_service.search(question, limit)
            query_results.append(search_results)
        results = sum(query_results, start=MemorySearchResult(memories=[]))
        results.memories.sort(key=lambda x: x.score, reverse=True)
        return results

    async def _normal_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        questions = await QueryPreprocessor.split(
            query,
            self.get_llm_client(self.db, self.ctx.memory_config.llm_model_id)
        )
        query_results = []
        for question in questions:
            search_results = await search_service.search(question, limit)
            query_results.append(search_results)
        results = sum(query_results, start=MemorySearchResult(memories=[]))
        results.memories.sort(key=lambda x: x.score, reverse=True)
        return results

    async def _quick_read(self, query: str, limit: int, includes=None) -> MemorySearchResult:
        search_service = self._get_search_service(includes)
        return await search_service.search(query, limit)
