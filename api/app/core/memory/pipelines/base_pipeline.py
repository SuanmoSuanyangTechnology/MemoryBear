import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memory.models.service_models import MemoryContext
from app.core.models import RedBearChatModel, RedBearEmbeddings, RedBearRerank
from app.integrations.model.invoke_backend import RemoteInvokeRef
from app.services.model_service import ModelApiKeyService


class ModelClientMixin(ABC):
    @staticmethod
    def get_llm_client(
        db: Session,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> RedBearChatModel:
        """LLM 壳（非解密引用）：凭据解密、渠道选路与 failover 在模型服务（设计 §2.2）。

        消费方一律 ``await``（``call_structured`` / ``ainvoke``）；构造本身不需要事件循环。
        """

        ref = ModelClientMixin._invoke_ref(db, model_id, tenant_id)
        return RedBearChatModel.for_invoke_ref(ref, params=extra_params or {})

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
    async def _invoke_ref_async(
        db: AsyncSession, model_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> RemoteInvokeRef:
        ref = await ModelApiKeyService.resolve_invoke_ref_async(db, model_id, tenant_id=tenant_id)
        if ref is None:
            raise ValueError(f"模型配置不可用: {model_id}")
        return ref

    @staticmethod
    async def get_llm_client_async(db: AsyncSession, model_id: uuid.UUID, tenant_id: uuid.UUID) -> RedBearChatModel:
        return RedBearChatModel.for_invoke_ref(
            await ModelClientMixin._invoke_ref_async(db, model_id, tenant_id)
        )

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


