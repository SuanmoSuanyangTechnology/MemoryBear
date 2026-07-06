import uuid
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session

from app.core.memory.models.service_models import MemoryContext
from app.core.models import RedBearModelConfig, RedBearLLM, RedBearEmbeddings, RedBearRerank
from app.services.model_service import ModelApiKeyService


class ModelClientMixin(ABC):
    @staticmethod
    def get_llm_client(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearLLM:
        api_config = ModelApiKeyService.get_available_api_key(db, model_id, tenant_id=tenant_id)
        return RedBearLLM(
            RedBearModelConfig(
                model_name=api_config.model_name,
                provider=api_config.provider,
                api_key=api_config.api_key,
                base_url=api_config.api_base,
                is_omni=api_config.is_omni
            )
        )

    @staticmethod
    def get_embedding_client(
        db: Session,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> RedBearEmbeddings:
        api_config = ModelApiKeyService.get_available_api_key(db, model_id, tenant_id=tenant_id)
        return RedBearEmbeddings(
            RedBearModelConfig(
                model_name=api_config.model_name,
                provider=api_config.provider,
                api_key=api_config.api_key,
                base_url=api_config.api_base,
            )
        )

    @staticmethod
    def get_rerank_client(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearRerank:
        api_config = ModelApiKeyService.get_available_api_key(db, model_id, tenant_id=tenant_id)
        return RedBearRerank(
            RedBearModelConfig(
                model_name=api_config.model_name,
                provider=api_config.provider,
                api_key=api_config.api_key,
                base_url=api_config.api_base,
            )
        )

    # ── Async variants ──────────────────────────────────────────

    @staticmethod
    async def _build_client_async(db, model_id: uuid.UUID, tenant_id: uuid.UUID, client_cls: type):
        """通用异步 client 构建：拉取 API key，组装 RedBearModelConfig，实例化 client_cls。"""
        api_config = await ModelApiKeyService.get_available_api_key_async(db, model_id, tenant_id=tenant_id)
        config = RedBearModelConfig(
            model_name=api_config.model_name,
            provider=api_config.provider,
            api_key=api_config.api_key,
            base_url=api_config.api_base,
            is_omni=api_config.is_omni,
        )
        return client_cls(config)

    @staticmethod
    async def get_llm_client_async(db, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearLLM:
        return await ModelClientMixin._build_client_async(db, model_id, tenant_id, RedBearLLM)

    @staticmethod
    async def get_embedding_client_async(db, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearEmbeddings:
        return await ModelClientMixin._build_client_async(db, model_id, tenant_id, RedBearEmbeddings)

    @staticmethod
    async def get_rerank_client_async(db, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearRerank:
        return await ModelClientMixin._build_client_async(db, model_id, tenant_id, RedBearRerank)


class BasePipeline(ABC):
    def __init__(self, ctx: MemoryContext):
        self.ctx = ctx

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        pass


