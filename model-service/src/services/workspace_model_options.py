"""Workspace model options (``/internal/v1/workspace-model-options``).

Owner-side port of the monolith's ``get_workspace_model_options`` catalog and
its preset-item renderer. The identity service assembles the public responses;
this module supplies the six-slot grouped catalog and renders preset ids into
option items.

Parity notes (source: ``core/api/app/services/workspace_service.py``):
- catalog rows: ``is_active`` and (own tenant or public speedbear), deprecated
  bases excluded, then rows with an empty channel candidate chain dropped;
- catalog cache: identical keys and TTL as the host so invalidation paths keep
  working across both processes;
- preset items: direct id load with no deprecation/channel filtering, exactly
  like ``_build_workspace_preset_response``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..infrastructure.redis_cache import (
    CACHE_MISS,
    get_json,
    get_workspace_model_public_version,
    set_json,
    workspace_model_options_key,
)
from ..models.models_model import (
    LLM_FAMILY_TYPES,
    ModelBase,
    ModelConfig,
    ModelProvider,
)
from .channel_registry import candidate_channels_batch_sync
from .model_profile_view import profile_of

logger = logging.getLogger(__name__)

_MODALITY_SLOTS = ("vision", "audio", "video")
_SLOT_MODALITIES = {"vision": "image", "audio": "audio", "video": "video"}
WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", *_MODALITY_SLOTS)
_CACHE_TTL_SECONDS = 300


def _serialize_option(model: ModelConfig) -> dict[str, Any]:
    profile = profile_of(model)
    return {
        "id": str(model.id),
        "name": model.name,
        "provider": getattr(model.provider, "value", model.provider),
        "type": getattr(model.type, "value", model.type),
        # 契约 v2 三列承载能力，旧 capability 键已从载荷下线
        "input_modalities": [str(item) for item in profile.input_modalities],
        "output_modalities": [str(item) for item in profile.output_modalities],
        "features": [str(item) for item in profile.features],
        "logo": model.logo,
        "is_public": bool(model.is_public),
        "is_deprecated": bool(
            getattr(model, "model_base", None) is not None
            and getattr(model.model_base, "is_deprecated", False)
        ),
    }


def _accessible_models(db: Session, tenant_id: uuid.UUID) -> list[ModelConfig]:
    return (
        db.query(ModelConfig)
        .options(joinedload(ModelConfig.model_base))
        .filter(ModelConfig.is_active.is_(True))
        .filter(
            or_(
                ModelConfig.tenant_id == tenant_id,
                (
                    (ModelConfig.provider == ModelProvider.SPEEDBEAR.value)
                    & ModelConfig.is_public.is_(True)
                ),
            )
        )
        .filter(~ModelConfig.model_base.has(ModelBase.is_deprecated.is_(True)))
        .all()
    )


def _drop_models_without_channel(
    db: Session, rows: list[ModelConfig], tenant_id: uuid.UUID
) -> list[ModelConfig]:
    if not rows:
        return rows
    chains = candidate_channels_batch_sync(db, rows, tenant_id)
    return [row for row in rows if chains.get(row.id)]


def _slot_matches(slot: str, model: ModelConfig) -> bool:
    model_type = str(model.type)
    if slot == "llm":
        return model_type in LLM_FAMILY_TYPES
    if slot == "embedding":
        return model_type == "embedding"
    if slot == "rerank":
        return model_type == "rerank"
    modality = _SLOT_MODALITIES.get(slot)
    if modality is None:
        return False
    profile = profile_of(model)
    return model_type in LLM_FAMILY_TYPES and modality in {
        str(item) for item in profile.input_modalities
    }


def _group_options(models: list[ModelConfig]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        slot: [] for slot in WORKSPACE_MODEL_SLOTS
    }
    seen_ids: set[str] = set()

    for model in models:
        model_id = str(model.id)
        if model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        data = _serialize_option(model)
        for slot in WORKSPACE_MODEL_SLOTS:
            if _slot_matches(slot, model):
                grouped[slot].append(data)

    return grouped


def get_workspace_model_options(
    db: Session, *, tenant_id: uuid.UUID
) -> dict[str, list[dict[str, Any]]]:
    """Six-slot catalog, cache-aside on the host's key and invalidation."""

    public_version = get_workspace_model_public_version()
    cache_key = workspace_model_options_key(tenant_id, public_version)
    cached = get_json(cache_key)
    if cached is not CACHE_MISS and isinstance(cached, dict):
        return cached

    rows = _accessible_models(db, tenant_id)
    result = _group_options(_drop_models_without_channel(db, rows, tenant_id))
    set_json(cache_key, result, _CACHE_TTL_SECONDS)
    return result


def _parse_model_ids(raw_ids: list[str]) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in raw_ids:
        try:
            model_id = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            # Invalid ids are misses, never a 500.
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        parsed.append(model_id)
    return parsed


def _load_models_by_ids(
    db: Session, model_ids: list[uuid.UUID]
) -> list[ModelConfig]:
    return (
        db.query(ModelConfig)
        .options(joinedload(ModelConfig.model_base))
        .filter(ModelConfig.id.in_(model_ids))
        .all()
    )


def get_workspace_model_option_items(
    db: Session, *, tenant_id: uuid.UUID, raw_ids: list[str]
) -> list[dict[str, Any]]:
    """Render preset ids as option items; misses are silently dropped."""

    model_ids = _parse_model_ids(raw_ids)
    if not model_ids:
        return []
    model_map = {model.id: model for model in _load_models_by_ids(db, model_ids)}
    items = [
        _serialize_option(model_map[model_id])
        for model_id in model_ids
        if model_id in model_map
    ]
    if len(items) != len(model_ids):
        logger.info(
            "workspace model option items: tenant=%s requested=%s hits=%s",
            tenant_id,
            len(model_ids),
            len(items),
        )
    return items


__all__ = [
    "WORKSPACE_MODEL_SLOTS",
    "get_workspace_model_option_items",
    "get_workspace_model_options",
]
