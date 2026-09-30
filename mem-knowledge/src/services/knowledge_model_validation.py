"""Validate caller-supplied knowledge model IDs using stored configuration only."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from redbear_model import ModelProfile
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import KnowledgeError
from ..models.references import ModelConfig, ModelProvider, ModelType
from ..repositories.reference import ReferenceRepository

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


def _matches_knowledge_model_field(field_name: str, model: ModelConfig) -> bool:
    """Match the same media capabilities and providers used by knowledge processing."""
    try:
        profile = ModelProfile.from_stored_fields(
            model_id=model.id,
            tenant_id=model.tenant_id,
            type=model.type,
            provider=model.provider,
            input_modalities=model.input_modalities or (),
            output_modalities=model.output_modalities or (),
            features=model.features or (),
            capabilities=model.capability or (),
            is_omni=bool(model.is_omni),
        )
    except (TypeError, ValueError):
        return False
    if profile.type.value != _MODEL_FIELD_TYPES[field_name].value:
        return False
    if field_name in _DASHSCOPE_MEDIA_FIELDS and model.provider != ModelProvider.DASHSCOPE:
        return False
    modality = _MEDIA_INPUT_MODALITIES.get(field_name)
    return modality is None or modality in profile.input_modalities


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

    models = await ReferenceRepository.get_model_configs(
        db, list(dict.fromkeys(requested.values()))
    )
    models_by_id = {model.id: model for model in models}
    for field_name, model_id in requested.items():
        model = models_by_id.get(model_id)
        # Do not disclose the state or capabilities of another tenant's private model.
        if model is None or not (
            model.tenant_id == tenant_id
            or (model.provider == ModelProvider.SPEEDBEAR and bool(model.is_public))
        ):
            raise KnowledgeError.from_code(
                "KB_KNOWLEDGE_MODEL_NOT_FOUND", params={"model_field": field_name}
            )
        if not model.is_active:
            raise KnowledgeError.from_code(
                "KB_KNOWLEDGE_MODEL_INACTIVE", params={"model_field": field_name}
            )

    # Batch-read base models explicitly instead of lazy-loading relationships in async code.
    base_ids = list(dict.fromkeys(model.model_id for model in models if model.model_id))
    bases = await ReferenceRepository.get_model_bases(db, base_ids)
    deprecated_ids = {base.id for base in bases if base.is_deprecated}
    for field_name, model_id in requested.items():
        model = models_by_id[model_id]
        if model.model_id in deprecated_ids:
            raise KnowledgeError.from_code(
                "KB_KNOWLEDGE_MODEL_DEPRECATED", params={"model_field": field_name}
            )
        if not _matches_knowledge_model_field(field_name, model):
            raise KnowledgeError.from_code(
                "KB_KNOWLEDGE_MODEL_CAPABILITY_MISMATCH", params={"model_field": field_name}
            )
