from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict

from app.core.memory.enums import StorageType
from app.schemas import MemoryConfig
from app.services.memory_config_service import MemoryConfigService


class MemoryContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    end_user_id: str
    memory_config: MemoryConfig
    storage_type: StorageType = StorageType.NEO4J
    user_rag_memory_id: str | None = None
    language: str = "zh"


class MemoryService:
    def __init__(
            self,
            db: Session,
            config_id: str,
            end_user_id: str,
            workspace_id: str | None = None,
            storage_type: str = "neo4j",
            user_rag_memory_id: str | None = None,
            language: str = "zh",
    ):
        config_service = MemoryConfigService(db)
        memory_config = config_service.load_memory_config(
            config_id=config_id,
            workspace_id=workspace_id,
            service_name="MemoryService",
        )
        self.ctx = MemoryContext(
            end_user_id=end_user_id,
            memory_config=memory_config,
            storage_type=StorageType(storage_type),
            user_rag_memory_id=user_rag_memory_id,
            language=language,
        )

    async def write(self, messages: list[dict]) -> str:
        raise NotImplementedError

    async def read(self, query: str, history: list, search_switch: str) -> dict:
        raise NotImplementedError

    async def forget(self, max_batch: int = 100, min_days: int = 30) -> dict:
        raise NotImplementedError

    async def reflect(self) -> dict:
        raise NotImplementedError

    async def cluster(self, new_entity_ids: list[str] = None) -> None:
        raise NotImplementedError
