from app.core.memory.enums import SearchStrategy
from app.core.memory.pipelines.base_pipeline import BasePipeline
from app.core.memory.read_services.query_preprocessor import QueryPreprocessor


class ReadPipeLine(BasePipeline):
    async def run(self, query, search_switch, memory_config):
        query = QueryPreprocessor.process(query)
        match search_switch:
            case SearchStrategy.DEEP:
                return await self._deep_read()
            case SearchStrategy.NORMAL:
                return await self._normal_read(query)
            case SearchStrategy.QUICK:
                return await self._quick_read()
            case _:
                raise RuntimeError("Unsupported search strategy")

    async def _deep_read(self):
        pass

    async def _normal_read(self, query):
        pass

    async def _quick_read(self):
        pass
