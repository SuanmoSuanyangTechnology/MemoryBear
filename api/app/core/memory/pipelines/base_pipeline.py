import uuid
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session

from app.core.memory.models.service_models import MemoryContext
from app.core.models import RedBearModelConfig, RedBearLLM, RedBearEmbeddings
from app.services.memory_config_service import MemoryConfigService
from app.services.model_service import ModelApiKeyService


class ModelClientMixin(ABC):
    @staticmethod
    def get_llm_client(db: Session, model_id: uuid.UUID) -> RedBearLLM:
        api_config = ModelApiKeyService.get_available_api_key(db, model_id)
        return RedBearLLM(
            RedBearModelConfig(
                model_name=api_config.model_name,
                provider=api_config.provider,
                api_key=api_config.api_key,
                base_url=api_config.api_base,
                is_omni=api_config.is_omni,
                support_thinking="thinking" in (api_config.capability or []),
            )
        )

    @staticmethod
    def get_embedding_client(db: Session, model_id: uuid.UUID) -> RedBearEmbeddings:
        config_service = MemoryConfigService(db)
        embedder_client_config = config_service.get_embedder_config(str(model_id))
        return RedBearEmbeddings(
            RedBearModelConfig(
                model_name=embedder_client_config["model_name"],
                provider=embedder_client_config["provider"],
                api_key=embedder_client_config["api_key"],
                base_url=embedder_client_config["base_url"],
            )
        )


class BasePipeline(ABC):
    def __init__(self, ctx: MemoryContext):
        self.ctx = ctx

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        pass


class DBRequiredPipeline(BasePipeline, ABC):
    def __init__(self, ctx: MemoryContext, db: Session):
        super().__init__(ctx)
        self.db = db
