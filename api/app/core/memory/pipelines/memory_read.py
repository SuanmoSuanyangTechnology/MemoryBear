from app.core.memory.enums import SearchStrategy
from app.core.memory.pipelines.base_pipeline import BasePipeline, ModelClientMixin
from app.core.memory.read_services.content_search import Neo4jSearchService
from app.core.memory.read_services.query_preprocessor import QueryPreprocessor


class ReadPipeLine(ModelClientMixin, BasePipeline):
    async def run(self, query: str, search_switch: SearchStrategy, limit: int = 10):
        query = QueryPreprocessor.process(query)
        match search_switch:
            case SearchStrategy.DEEP:
                return await self._deep_read()
            case SearchStrategy.NORMAL:
                return await self._normal_read(query)
            case SearchStrategy.QUICK:
                return await self._quick_read(query, limit)
            case _:
                raise RuntimeError("Unsupported search strategy")

    async def _deep_read(self):
        pass

    async def _normal_read(self, query):
        pass

    async def _quick_read(self, query, limit):
        search_service = Neo4jSearchService(
            self.ctx,
            self.get_embedding_client(self.db, self.ctx.memory_config.embedding_model_id)
        )
        return await search_service.search(query, limit)
