import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memory.models.service_models import MemoryContext
from app.core.models import RedBearModelConfig, RedBearLLM, RedBearEmbeddings, RedBearRerank
from app.integrations.model.invoke_backend import RemoteInvokeRef
from app.services.model_service import ModelApiKeyService


class ModelClientMixin(ABC):
    @staticmethod
    def get_llm_client(
        db: Session,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> RedBearLLM:
        api_config = ModelApiKeyService.get_available_api_key(db, model_id, tenant_id=tenant_id)
        return RedBearLLM(
            RedBearModelConfig.from_api_key(api_config, extra_params=extra_params or {})
        )

    @staticmethod
    def _invoke_ref(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RemoteInvokeRef:
        """非解密引用：凭据解密与选路在模型服务（§2.2）。"""
        ref = ModelApiKeyService.resolve_invoke_ref(db, model_id, tenant_id=tenant_id)
        if ref is None:
            raise ValueError(f"模型配置不可用: {model_id}")
        return ref

    @staticmethod
    def get_embedding_client(
        db: Session,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> RedBearEmbeddings:
        return RedBearEmbeddings.for_invoke(ModelClientMixin._invoke_ref(db, model_id, tenant_id))

    @staticmethod
    def get_rerank_client(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearRerank:
        return RedBearRerank.for_invoke(ModelClientMixin._invoke_ref(db, model_id, tenant_id))

    # ── Async variants ──────────────────────────────────────────

    @staticmethod
    async def _build_client_async(db: AsyncSession, model_id: uuid.UUID, tenant_id: uuid.UUID, client_cls: type):
        """通用异步 client 构建：拉取 API key，组装 RedBearModelConfig，实例化 client_cls。"""
        api_config = await ModelApiKeyService.get_available_api_key_async(db, model_id, tenant_id=tenant_id)
        config = RedBearModelConfig.from_api_key(api_config)
        return client_cls(config)

    @staticmethod
    async def _invoke_ref_async(
        db: AsyncSession, model_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> RemoteInvokeRef:
        ref = await ModelApiKeyService.resolve_invoke_ref_async(db, model_id, tenant_id=tenant_id)
        if ref is None:
            raise ValueError(f"模型配置不可用: {model_id}")
        return ref

    @staticmethod
    async def get_llm_client_async(db: AsyncSession, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearLLM:
        return await ModelClientMixin._build_client_async(db, model_id, tenant_id, RedBearLLM)

    @staticmethod
    async def get_embedding_client_async(db: AsyncSession, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearEmbeddings:
        return RedBearEmbeddings.for_invoke(
            await ModelClientMixin._invoke_ref_async(db, model_id, tenant_id)
        )

    @staticmethod
    async def get_rerank_client_async(db: AsyncSession, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearRerank:
        return RedBearRerank.for_invoke(
            await ModelClientMixin._invoke_ref_async(db, model_id, tenant_id)
        )


class BasePipeline(ABC):
    def __init__(self, ctx: MemoryContext):
        self.ctx = ctx

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        pass


