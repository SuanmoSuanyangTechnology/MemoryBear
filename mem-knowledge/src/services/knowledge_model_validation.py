"""Validate caller-supplied knowledge model IDs through the shared model resolver."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from redbear_model import (
    ModelProvider,
    ModelType,
    RedBearModelError,
    ResolvedModelConfig,
    resolve_model_async,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import KnowledgeError
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


def _matches_knowledge_model_field(field_name: str, model: ResolvedModelConfig) -> bool:
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
    resolved_models: dict[uuid.UUID, ResolvedModelConfig] = {}
    for field_name, model_id in requested.items():
        if model_id not in resolved_models:
            try:
                resolved_models[model_id] = await resolve_model_async(
                    registry, model_config_id=model_id, tenant_id=tenant_id
                )
            except (RedBearModelError, ValueError, InvalidTag) as exc:
                # The resolver checks state before visibility; use one public error to
                # avoid disclosing private model state or credential details.
                raise KnowledgeError.from_code(
                    "KB_KNOWLEDGE_MODEL_UNAVAILABLE", params={"model_field": field_name}
                ) from exc
        model = resolved_models[model_id]
        if not _matches_knowledge_model_field(field_name, model):
            raise KnowledgeError.from_code(
                "KB_KNOWLEDGE_MODEL_CAPABILITY_MISMATCH", params={"model_field": field_name}
            )
