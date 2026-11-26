"""Workspace model-selection validation for the host's create/update gate.

Replaces the monolith's direct ``model_configs`` reads (its
``workspace_service`` module) with an owner-side check: the host posts its
slot selection, this service runs the same static gate and live probes
against its own tables. Issue dicts are language-neutral; the host renders
localized messages.

Parity notes (source: ``core/api/app/services/workspace_service.py``):
- required slots: llm/embedding/rerank; modality slots (vision/audio/video)
  are input facets of LLM-family rows; ``image2text`` aliases to ``vision``;
- static diagnosis order: not_found -> not_accessible (cross-tenant, no name
  leak) -> inactive -> deprecated -> not_accessible;
- runtime probe order: get_model_by_id -> visibility -> inactive ->
  capability -> validate_model_config -> embedding dimension guard.

The async route over a request-scoped sync session mirrors the existing
``/internal/v1/models/validate`` endpoint (management-plane sync decision).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..errors import BizCode, BusinessException
from ..models.models_model import LLM_FAMILY_TYPES, ModelConfig, ModelProvider
from .model_profile_view import profile_of
from .model_service import ModelConfigService

logger = logging.getLogger(__name__)

_MODALITY_SLOTS = ("vision", "audio", "video")
_SLOT_MODALITIES = {"vision": "image", "audio": "audio", "video": "video"}
_SLOT_ALIASES = {"image2text": "vision"}
WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", *_MODALITY_SLOTS)
REQUIRED_WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank")
_VALIDATE_AS_LLM_SLOTS = {*_MODALITY_SLOTS, *_SLOT_ALIASES}

# Mirrors the host ES index definitions (EMBEDDING_DIMENSIONS); the storage
# split owns the authoritative tuple, this copy guards the same values.
EMBEDDING_DIMENSIONS = (768, 1024, 1536, 2048, 3072, 4096)


def _issue(
    slot: str,
    reason: str,
    *,
    model_id: Any = None,
    model_name: str | None = None,
    provider: str | None = None,
    model_type: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "slot": slot,
        "reason": reason,
        "model_id": str(model_id) if model_id else None,
        "model_name": model_name,
        "provider": provider,
        "model_type": model_type,
        "detail": detail,
    }


def _provider_value(model: Any) -> str | None:
    provider = getattr(model, "provider", None)
    return getattr(provider, "value", provider)


def _accessible_models(db: Session, tenant_id: uuid.UUID) -> list[Any]:
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
        .all()
    )


def _slot_matches(slot: str, model: Any) -> bool:
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


def _diagnose_unavailable(
    db: Session, slot: str, model_id: str, tenant_id: uuid.UUID
) -> dict[str, Any]:
    model = None
    try:
        model = (
            db.query(ModelConfig)
            .options(joinedload(ModelConfig.model_base))
            .filter(ModelConfig.id == uuid.UUID(str(model_id)))
            .first()
        )
    except Exception:  # invalid UUID / query failure -> "not found"
        model = None

    if model is None:
        return _issue(slot, "not_found", model_id=model_id)

    common = {
        "model_id": model_id,
        "model_name": model.name,
        "provider": _provider_value(model),
        "model_type": str(model.type),
    }
    is_tenant_model = model.tenant_id == tenant_id
    is_public_speedbear = (
        model.provider == ModelProvider.SPEEDBEAR.value and bool(model.is_public)
    )
    if not (is_tenant_model or is_public_speedbear):
        # Cross-tenant rows echo only the requested id (no name/provider leak).
        return _issue(slot, "not_accessible", model_id=model_id)
    if not model.is_active:
        return _issue(slot, "inactive", **common)
    if getattr(model, "model_base", None) is not None and model.model_base.is_deprecated:
        return _issue(slot, "deprecated", **common)
    return _issue(slot, "not_accessible", **common)


def _collect_static_issues(
    db: Session, tenant_id: uuid.UUID, selection: dict[str, str | None]
) -> list[dict[str, Any]]:
    available = {str(model.id): model for model in _accessible_models(db, tenant_id)}
    issues: list[dict[str, Any]] = []
    # Partial payloads validate only the slots the caller supplied; absent
    # required slots are reported when the whole selection is untouched, and
    # an explicit null is always a "not configured" assertion for the slot.
    configured_any = any(selection.get(slot) for slot in WORKSPACE_MODEL_SLOTS)

    for slot in WORKSPACE_MODEL_SLOTS:
        raw_value = selection.get(slot)
        if raw_value is None:
            if slot in REQUIRED_WORKSPACE_MODEL_SLOTS and (
                not configured_any or slot in selection
            ):
                issues.append(_issue(slot, "not_configured"))
            continue
        model_id = str(raw_value)
        model = available.get(model_id)
        if model is None:
            issues.append(_diagnose_unavailable(db, slot, model_id, tenant_id))
            continue
        if not _slot_matches(slot, model):
            issues.append(
                _issue(
                    slot,
                    "capability_mismatch",
                    model_id=model_id,
                    model_name=model.name,
                    provider=_provider_value(model),
                    model_type=str(model.type),
                )
            )
    return issues


async def _probe_slot(
    db: Session, slot: str, model_id: str, tenant_id: uuid.UUID
) -> dict[str, Any] | None:
    try:
        model = ModelConfigService.get_model_by_id(
            db, uuid.UUID(str(model_id)), tenant_id
        )
    except BusinessException as exc:
        reason = "deprecated" if exc.code == BizCode.MODEL_DEPRECATED else "not_found"
        return _issue(slot, reason, model_id=model_id, detail=exc.message)
    except Exception as exc:
        return _issue(slot, "not_found", model_id=model_id, detail=str(exc))

    issue_context = {
        "model_id": model_id,
        "model_name": model.name,
        "provider": _provider_value(model),
        "model_type": str(model.type),
    }
    is_tenant_model = model.tenant_id == tenant_id
    is_public_speedbear = (
        model.provider == ModelProvider.SPEEDBEAR.value and bool(model.is_public)
    )
    if not (is_tenant_model or is_public_speedbear):
        return _issue(slot, "not_accessible", model_id=model_id)
    if not model.is_active:
        return _issue(slot, "inactive", **issue_context)
    if not _slot_matches(_SLOT_ALIASES.get(slot, slot), model):
        return _issue(slot, "capability_mismatch", **issue_context)

    validate_type = "llm" if slot in _VALIDATE_AS_LLM_SLOTS else slot
    try:
        result = await ModelConfigService.validate_model_config(
            db=db,
            model_config_id=model.id,
            model_type=validate_type,
            tenant_id=tenant_id,
        )
    except BusinessException as exc:
        reason = (
            "no_api_key"
            if exc.code in (BizCode.MODEL_NOT_FOUND, BizCode.NO_AVAILABLE_CHANNEL)
            else "verify_failed"
        )
        return _issue(slot, reason, detail=exc.message, **issue_context)
    except Exception as exc:
        return _issue(slot, "verify_failed", detail=str(exc), **issue_context)

    if not result.get("valid"):
        return _issue(
            slot,
            "api_verify_failed",
            detail=result.get("error") or result.get("message"),
            **issue_context,
        )

    if slot == "embedding":
        dimension = (result.get("usage") or {}).get("vector_dimension")
        if not isinstance(dimension, int) or dimension not in EMBEDDING_DIMENSIONS:
            return _issue(
                slot,
                "unsupported_dimension",
                detail=(
                    f"vector_dimension={dimension}, "
                    f"supported={list(EMBEDDING_DIMENSIONS)}"
                ),
                **issue_context,
            )
    return None


async def validate_selection(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    selection: dict[str, str | None],
    static_check: bool = False,
    probe_slots: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the host's static gate or the requested runtime probes."""

    if static_check:
        return {"issues": _collect_static_issues(db, tenant_id, selection)}

    allowed = {*WORKSPACE_MODEL_SLOTS, *_SLOT_ALIASES}
    unknown = [slot for slot in probe_slots if slot not in allowed]
    if unknown:
        raise BusinessException(
            f"unknown workspace model slots: {', '.join(sorted(unknown))}",
            BizCode.INVALID_PARAMETER,
        )

    issues: list[dict[str, Any]] = []
    for slot in probe_slots:
        model_id = selection.get(slot)
        if not model_id:
            issues.append(_issue(slot, "not_configured"))
            continue
        issue = await _probe_slot(db, slot, str(model_id), tenant_id)
        if issue is not None:
            issues.append(issue)
    return {"issues": issues}
