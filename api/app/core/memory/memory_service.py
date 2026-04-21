from sqlalchemy.orm import Session

from app.core.memory.enums import StorageType, SearchStrategy
from app.core.memory.models.service_models import MemoryContext, MemorySearchResult
from app.core.memory.pipelines.memory_read import ReadPipeLine
from app.db import get_db_context
from app.services.memory_config_service import MemoryConfigService


class MemoryService:
    def __init__(
            self,
            db: Session,
            config_id: str | None,
            end_user_id: str,
            workspace_id: str | None = None,
            storage_type: str = "neo4j",
            user_rag_memory_id: str | None = None,
            language: str = "zh",
    ):
        config_service = MemoryConfigService(db)
        memory_config = None
        if config_id is not None:
            memory_config = config_service.load_memory_config(
                config_id=config_id,
                workspace_id=workspace_id,
                service_name="MemoryService",
            )
        if memory_config is None and storage_type.lower() == "neo4j":
            raise RuntimeError("Memory configuration for unspecified users")
        self.ctx = MemoryContext(
            end_user_id=end_user_id,
            memory_config=memory_config,
            storage_type=StorageType(storage_type),
            user_rag_memory_id=user_rag_memory_id,
            language=language,
        )

    async def write(self, messages: list[dict]) -> str:
        raise NotImplementedError

    async def read(
            self,
            query: str,
            search_switch: SearchStrategy,
            limit: int = 10,
    ) -> MemorySearchResult:
        with get_db_context() as db:
            return await ReadPipeLine(self.ctx, db).run(query, search_switch, limit)

    async def forget(self, max_batch: int = 100, min_days: int = 30) -> dict:
        raise NotImplementedError

    async def reflect(self) -> dict:
        raise NotImplementedError

    async def cluster(self, new_entity_ids: list[str] = None) -> None:
        raise NotImplementedError
