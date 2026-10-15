"""Validate caller-supplied knowledge model IDs against the shared model registry."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from redbear_model import (
    ModelConfigSnapshot,
    ModelProvider,
    ModelType,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..errors import KnowledgeError
from ..integrations.model.errors import ModelInvokeClientError
from ..integrations.model.invoke import ModelCallContext
from ..integrations.model.runtime import ModelInvokeRuntime
from ..repositories.model_registry import AsyncSQLModelRegistry

_MODEL_FIELD_TYPES = {
    "embedding_id": ModelType.EMBEDDING,
    "reranker_id": ModelType.RERANK,
    "llm_id": ModelType.LLM,
    "image2text_id": ModelType.LLM,
    "audio2text_id": ModelType.ASR,
    "video2text_id": ModelType.LLM,
}
_MEDIA_INPUT_MODALITIES = {
    "image2text_id": "image",
    "video2text_id": "video",
}
_DASHSCOPE_MEDIA_FIELDS = frozenset({"audio2text_id", "video2text_id"})


async def validate_requested_knowledge_model_availability(
    values: Mapping[str, uuid.UUID | None],
    principal: Principal,
    model_runtime: ModelInvokeRuntime,
) -> None:
    """Require explicit model bindings to be available before mutation side effects."""
    checked: set[uuid.UUID] = set()
    context = ModelCallContext(
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        actor_name=principal.actor_name,
        workspace_id=principal.workspace_id,
    )
    for field_name in _MODEL_FIELD_TYPES:
        model_id = values.get(field_name)
        if model_id is None or model_id in checked:
            continue
        try:
            available = await model_runtime.invoke_client.is_model_available(model_id, context)
        except ModelInvokeClientError as exc:
            raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE") from exc
        if not available:
            raise KnowledgeError.from_code(
                "KB_KNOWLEDGE_MODEL_UNAVAILABLE", params={"model_field": field_name}
            )
        checked.add(model_id)


def _matches_knowledge_model_field(field_name: str, model: ModelConfigSnapshot) -> bool:
    """Match the same media capabilities and providers used by knowledge processing."""
    if model.profile.type != _MODEL_FIELD_TYPES[field_name]:
        return False
    if field_name in _DASHSCOPE_MEDIA_FIELDS and model.provider != ModelProvider.DASHSCOPE:
        return False
    modality = _MEDIA_INPUT_MODALITIES.get(field_name)
    return modality is None or modality in model.profile.input_modalities


async def validate_requested_knowledge_models(
    db: AsyncSession,
    values: Mapping[str, uuid.UUID | None],
    tenant_id: uuid.UUID,
) -> None:
    """Check non-null IDs from the original request, never inherited or stored bindings."""
    requested = {
        field_name: model_id
        for field_name in _MODEL_FIELD_TYPES
        if (model_id := values.get(field_name)) is not None
    }
    if not requested:
        return

    registry = AsyncSQLModelRegistry(db)
    configs: dict[uuid.UUID, ModelConfigSnapshot] = {}
    for field_name, model_id in requested.items():
        if model_id not in configs:
            try:
                config = await registry.get_model_config(model_id, tenant_id)
            except Exception as exc:
                raise KnowledgeError.from_code(
                    "KB_KNOWLEDGE_MODEL_UNAVAILABLE", params={"model_field": field_name}
                ) from exc
            # Fold missing, invisible, deprecated and inactive configs into one public
            # error to avoid disclosing model state.
            if config is None or config.is_deprecated or not config.is_active:
                raise KnowledgeError.from_code(
                    "KB_KNOWLEDGE_MODEL_UNAVAILABLE", params={"model_field": field_name}
                )
            configs[model_id] = config
        model = configs[model_id]
        if not _matches_knowledge_model_field(field_name, model):
            raise KnowledgeError.from_code(
                "KB_KNOWLEDGE_MODEL_CAPABILITY_MISMATCH", params={"model_field": field_name}
            )
