import uuid
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session

from app.core.memory.llm_tools import OpenAIEmbedderClient
from app.core.memory.models.service_models import MemoryContext
from app.core.models import RedBearModelConfig
from app.services.memory_config_service import MemoryConfigService


class ModelClientMixin(ABC):
    @staticmethod
    def get_llm_client(db: Session, model_id: uuid.UUID):
        pass

    @staticmethod
    def get_embedding_client(db: Session, model_id: uuid.UUID) -> OpenAIEmbedderClient:
        config_service = MemoryConfigService(db)
        embedder_client_config = config_service.get_embedder_config(str(model_id))
        return OpenAIEmbedderClient(
            RedBearModelConfig(
                model_name=embedder_client_config["model_name"],
                provider=embedder_client_config["provider"],
                api_key=embedder_client_config["api_key"],
                base_url=embedder_client_config["base_url"],
            )
        )


class BasePipeline(ABC):
    def __init__(self, ctx: MemoryContext, db: Session):
        self.ctx = ctx
        self.db = db

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        pass
