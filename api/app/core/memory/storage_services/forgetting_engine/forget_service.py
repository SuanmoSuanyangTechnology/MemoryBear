from app.core.memory.models.service_models import MemoryContext
from app.repositories.neo4j import Neo4jConnector


class ForgetService:
    def __init__(
            self,
            ctx: MemoryContext,
            memory_limit: int,
    ):
        self.ctx = ctx
        self.memory_limit = memory_limit

    async def run(self):
        async with Neo4jConnector() as connector:
            pass

    def important_rank(self):
        pass

    async def chunk_stmt_clean(self):
        pass

    async def entity_clean(self):
        pass
