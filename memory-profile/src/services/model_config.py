"""Resolve the models a workspace should call, and build clients for them.

Mirrors the API's resolution order without loading the full memory config:
workspace → active ``memory_config`` → the slot's model id (default-config
workspaces follow the singleton preset) → ``model_configs`` tenant/active check.

A workspace configures six slots — ``llm``, ``embedding``, ``rerank``,
``vision``, ``audio`` and ``video``. Chat slots are all invoked through the LLM
family (the API does the same: vision/audio/video ids go to
``get_llm_client_async``), while embedding and rerank have their own call shapes.

Callers resolve the end user through :class:`EndUserService` first, so the
user → workspace hop is not repeated here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from src.infrastructure.model_service import (
    ModelServiceManager,
    ProfileChatModel,
    RerankHit,
)
from src.infrastructure.postgres import PostgresManager
from src.repositories.model_config import ModelConfigRepository
from src.schemas.memory_storage import ModelInfo
from src.services.model_profile_view import normalize_type, profile_of
from redbear_model.contracts import ModelType

logger = logging.getLogger(__name__)


class ModelConfigurationError(RuntimeError):
    """Raised when no usable model configuration exists for the requested slot."""


class ChatModelProvider(Protocol):
    """Anything that can hand out a chat model for one end user."""

    async def chat_model(self, end_user_id: UUID) -> ProfileChatModel: ...


class ModelSlot(StrEnum):
    """A workspace model column (api ``_effective_workspace_models`` keys)."""

    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    VISION = "vision"
    AUDIO = "audio"
    VIDEO = "video"


#: Slots invoked through the chat/LLM family; the other two have typed call shapes.
_CHAT_SLOTS = frozenset({ModelSlot.LLM, ModelSlot.VISION, ModelSlot.AUDIO, ModelSlot.VIDEO})

#: Human-readable names used in errors (the API's messages say "LLM model").
_SLOT_LABELS = {
    ModelSlot.LLM: "LLM",
    ModelSlot.EMBEDDING: "embedding",
    ModelSlot.RERANK: "rerank",
    ModelSlot.VISION: "vision",
    ModelSlot.AUDIO: "audio",
    ModelSlot.VIDEO: "video",
}


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """A validated ``model_configs`` row plus the tenant it is called for."""

    model_config_id: UUID
    tenant_id: UUID
    slot: ModelSlot = ModelSlot.LLM


class ModelConfigService:
    """Answer "which model serves this workspace slot?" for every module."""

    def __init__(
        self,
        repository: ModelConfigRepository,
        model_service: ModelServiceManager,
    ) -> None:
        self.repository = repository
        self.model_service = model_service

    @classmethod
    def from_postgres(
        cls,
        postgres: PostgresManager,
        model_service: ModelServiceManager,
    ) -> ModelConfigService:
        """Build the service with its default repository (the composition path)."""

        return cls(ModelConfigRepository(postgres), model_service)

    async def resolve_workspace(
        self,
        workspace: Mapping[str, Any],
        slot: ModelSlot = ModelSlot.LLM,
    ) -> ResolvedModel:
        """Resolve a slot from an already-loaded workspace row.

        The row is already in memory, so the remaining facts — config
        existence, the preset/slot model id and the ``model_configs`` row — are
        fetched in one statement and one session. The model configuration is a
        workspace-level fact, so a batch over one workspace resolves it once
        instead of once per end user.
        """

        slot = ModelSlot(slot)
        label = _SLOT_LABELS[slot]
        workspace_id = workspace["id"]
        config_id = workspace["memory_config"]
        if config_id is None:
            raise ModelConfigurationError(f"空间{workspace_id}无启用的记忆配置")
        row = await self.repository.resolve_model(
            config_id=config_id,
            slot=slot.value,
            is_default_config=bool(workspace["is_default_config"]),
            workspace_model_id=self._parse_uuid(workspace[slot.value], slot.value),
        )
        if row is None or not row["config_exists"]:
            raise ModelConfigurationError(f"记忆配置 {config_id} 不可用")
        model_id = row["model_id"]
        if model_id is None:
            raise ModelConfigurationError(f"记忆配置 {config_id} 未配置 {label} 模型")
        if row["model_row_id"] is None:
            raise ModelConfigurationError(f"{label} 模型 {model_id} 不存在")
        if not row["is_active"]:
            raise ModelConfigurationError(f"{label} 模型 {model_id} 已停用")

        tenant_id = workspace["tenant_id"]
        if tenant_id is None:
            raise ModelConfigurationError(f"workspace {workspace_id} 缺少租户")
        if not row["is_public"] and row["tenant_id"] != tenant_id:
            raise ModelConfigurationError(f"{label} 模型 {model_id} 不属于当前租户")

        return ResolvedModel(model_config_id=model_id, tenant_id=tenant_id, slot=slot)

    async def chat_model_for_workspace(
        self,
        workspace: Mapping[str, Any],
        slot: ModelSlot = ModelSlot.LLM,
    ) -> ProfileChatModel:
        """Build a chat model for a chat slot (default ``llm``)."""

        slot = ModelSlot(slot)
        if slot not in _CHAT_SLOTS:
            raise ValueError(f"model slot {slot.value!r} is not invoked as a chat model")
        resolved = await self.resolve_workspace(workspace, slot)
        return await self.model_service.chat_model(
            resolved.model_config_id, resolved.tenant_id
        )

    async def embed_texts_for_workspace(
        self,
        workspace: Mapping[str, Any],
        texts: Sequence[str],
    ) -> list[list[float]]:
        """Embed a batch of texts with the workspace's embedding slot."""

        resolved = await self.resolve_workspace(workspace, ModelSlot.EMBEDDING)
        return await self.model_service.embed_texts(
            resolved.model_config_id, resolved.tenant_id, list(texts)
        )

    async def rerank_for_workspace(
        self,
        workspace: Mapping[str, Any],
        *,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> list[RerankHit]:
        """Score documents against ``query`` with the workspace's rerank slot."""

        resolved = await self.resolve_workspace(workspace, ModelSlot.RERANK)
        return await self.model_service.rerank(
            resolved.model_config_id,
            resolved.tenant_id,
            query=query,
            documents=list(documents),
            top_n=top_n,
        )

    def workspace_memo(self, workspace: Mapping[str, Any]) -> WorkspaceModelMemo:
        return WorkspaceModelMemo(self, workspace)

    @staticmethod
    def _parse_uuid(value: object, label: str) -> UUID | None:
        if isinstance(value, UUID):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return UUID(value.strip())
            except ValueError:
                logger.warning("invalid %s model id: %r", label, value)
        return None


    @staticmethod
    async def get_model_by_id_async(
        postgres: PostgresManager,
        model_id: UUID,
        tenant_id: UUID | None = None,
    ):
        """根据ID异步获取模型配置"""
        model = await ModelConfigRepository(postgres).get_runtime_model(model_id, tenant_id=tenant_id)
        if not model:
            raise ModelConfigurationError("模型配置不存在")
        if model.model_base and model.model_base.is_deprecated:
            raise ModelConfigurationError(
                f"模型 '{model.name}' 已弃用，请在模型配置中更换为其他模型",
            )
        return model

    @staticmethod
    def _runtime_model_view(
        model, tenant_id: UUID | None = None
    ) -> ModelInfo:
        """非解密视图的公共映射：能力/模态由 profile 派生，凭据与渠道恒空。

        归属租户取**调用租户**（tenant_id 非空时）：公共配置（speedbear 等）挂在系统
        租户下，服务侧按请求租户加载候选渠道（平台渠道在各调用租户名下）且用量归因落
        调用租户；无租户上下文（tenant_id=None）退化为配置行租户（运行面兜底语义）。
        """

        profile = profile_of(model)
        effective_tenant = tenant_id or model.tenant_id
        return ModelInfo(
            model_name=model.name,
            model_type=ModelType(normalize_type(model.type)),
            api_key=None,
            provider=model.provider,
            input_modalities=[str(item.value) for item in profile.input_modalities],
            output_modalities=[str(item.value) for item in profile.output_modalities],
            features=[str(item.value) for item in profile.features],
            tenant_id=str(effective_tenant) if effective_tenant is not None else None,
            model_config_id=str(model.id),
            channel_id=None,
        )

    @staticmethod
    async def get_runtime_model_view_async(
        postgres: PostgresManager,
        model_id: UUID,
        tenant_id: UUID | None = None,
    ) -> ModelInfo:
        """运行期**非解密**模型视图（G2/G3 invoke 接缝）：`api_key` 恒 None、不带换线计划。

        能力/模态由 profile 派生，可见性（不存在 / 已弃用 / 跨租户）沿用
        :meth:`get_model_by_id_async` 的报错语义；凭据可用性与选路交给
        模型服务在调用时判定（渠道禁用等在那里才有完整事实，见 `channel_registry`）。
        归属租户取调用租户（公共配置挂系统租户，选路与归因须落调用方，见
        :meth:`_runtime_model_view`）。
        """

        model = await ModelConfigService.get_model_by_id_async(postgres, model_id, tenant_id=tenant_id)
        return ModelConfigService._runtime_model_view(model, tenant_id)


class WorkspaceModelMemo:
    """Request-scoped memo for the workspace-level LLM.

    Generation resolves the model at the API's original point (only after the
    user/source checks say there is something to generate), so the first
    resolution serves the rest of the request: both parts of a single user and
    every user in a batch. A failed resolution is remembered too, so N users do
    not retry it N times; the message matches what the API would have reported
    for each user.
    """

    def __init__(
        self, resolver: ModelConfigService, workspace: Mapping[str, Any]
    ) -> None:
        self._resolver = resolver
        self._workspace = workspace
        self._llm: ProfileChatModel | None = None
        self._error: str | None = None
        self._lock = asyncio.Lock()

    async def chat_model(self, end_user_id: UUID) -> ProfileChatModel:
        del end_user_id
        if self._llm is not None:
            return self._llm
        if self._error is not None:
            raise ModelConfigurationError(self._error)
        async with self._lock:
            if self._llm is None and self._error is None:
                try:
                    self._llm = await self._resolver.chat_model_for_workspace(
                        self._workspace
                    )
                except Exception as exc:
                    self._error = str(exc)
                    raise
        if self._llm is None:
            raise ModelConfigurationError(self._error or "LLM 配置不可用")
        return self._llm
