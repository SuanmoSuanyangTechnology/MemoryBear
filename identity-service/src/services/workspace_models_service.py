"""Workspace model configuration service for the W2-A public surface.

Identity owns the workspace rows but not the model tables: preset rendering
and the candidate catalog come from model-service. The workspace-model write
unit stays in the monolith during the coexistence period (decision D1) so the
workspace-column update and the reembed-job insert commit in one transaction;
this service only proxies that call and reads the active job id.

Validation semantics mirror the host ``workspace_service``: explicit
``is_default_config`` wins, otherwise the presence of any slot key means a
non-default target, otherwise the workspace's current mode applies. A default
target probes all six slots; an explicit target runs the static gate first
and then probes the three required slots.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from error_core.codes import BizCode
from sqlalchemy.orm import Session

from src.core.exceptions import BusinessException
from src.i18n import translate
from src.integrations import model_service, monolith
from src.models.user_model import User
from src.models.workspace_model import Workspace
from src.repositories import management, workspace_repository
from src.schemas.workspace_schema import WorkspaceModelsUpdate
from src.services import workspace_service

logger = logging.getLogger(__name__)

WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", "vision", "audio", "video")
REQUIRED_WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank")


def _workspace_or_not_found(db: Session, workspace_id: uuid.UUID) -> Workspace:
    workspace = management.get_workspace_by_id(db, workspace_id)
    if workspace is None:
        logger.warning("工作空间不存在: %s", workspace_id)
        raise BusinessException("工作空间不存在", code=BizCode.WORKSPACE_NOT_FOUND)
    return workspace


def _build_workspace_models_response(
    source: dict[str, Any], *, locale: str
) -> dict[str, Any]:
    values = {slot: source.get(slot) for slot in WORKSPACE_MODEL_SLOTS}
    notice_pending = bool(source.get("default_model_notice_pending"))
    return {
        **values,
        "is_default_config": bool(source.get("is_default_config")),
        "default_config_updated": notice_pending,
        "default_config_notice": (
            translate("workspace.models.default_config_updated_notice", locale)
            if notice_pending
            else None
        ),
    }


def get_default_workspace_models(
    db: Session, *, actor: User, language: str
) -> dict[str, Any]:
    """Render the default preset; an unset preset yields an empty payload."""

    preset = workspace_repository.get_default_model_preset(db)
    if preset is None:
        logger.info("默认模型配置未设置，返回空配置")
        return {}

    slot_to_model_id = {
        "llm": preset.llm_model_config_id,
        "embedding": preset.embedding_model_config_id,
        "rerank": preset.rerank_model_config_id,
        "vision": preset.vision_model_config_id,
        "audio": preset.audio_model_config_id,
        "video": preset.video_model_config_id,
    }
    raw_ids = [str(model_id) for model_id in slot_to_model_id.values() if model_id]
    items = model_service.get_workspace_model_option_items(
        tenant_id=actor.tenant_id,
        actor_id=actor.id,
        actor_name=actor.username,
        raw_ids=raw_ids,
        language=language,
    )
    items_by_id = {str(item.get("id")): item for item in items}

    result: dict[str, Any] = {}
    for slot, model_id in slot_to_model_id.items():
        item = items_by_id.get(str(model_id)) if model_id else None
        if item is None:
            logger.error(
                "默认模型配置缺少 %s 模型: preset=%s, model_id=%s",
                slot,
                preset.id,
                model_id,
            )
            raise BusinessException(
                f"默认模型配置缺少 {slot} 模型", code=BizCode.MODEL_NOT_FOUND
            )
        result[slot] = item
    return result


def get_workspace_model_options(*, actor: User, language: str) -> dict[str, Any]:
    """Six-slot candidate catalog; model-service owns grouping and caching."""

    return model_service.get_workspace_model_options(
        tenant_id=actor.tenant_id,
        actor_id=actor.id,
        actor_name=actor.username,
        language=language,
    )


def get_workspace_models(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor: User,
    locale: str,
) -> dict[str, Any]:
    """Workspace slots + active reembed job id (job lookup degrades to None)."""

    workspace = _workspace_or_not_found(db, workspace_id)
    response = _build_workspace_models_response(
        {
            **{slot: getattr(workspace, slot) for slot in WORKSPACE_MODEL_SLOTS},
            "is_default_config": workspace.is_default_config,
            "default_model_notice_pending": workspace.default_model_notice_pending,
        },
        locale=locale,
    )
    response["reembed_job_id"] = monolith.get_active_workspace_reembed_job_id(
        workspace_id=workspace.id,
        tenant_id=workspace.tenant_id,
        user_id=actor.id,
        language=locale,
    )
    return response


def validate_workspace_models(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor: User,
    models_update: WorkspaceModelsUpdate | None,
    locale: str,
) -> dict[str, Any]:
    """Dry-run validation; never writes. Mirrors the host target resolution."""

    workspace = _workspace_or_not_found(db, workspace_id)
    selection: dict[str, Any] = {
        slot: getattr(workspace, slot) for slot in WORKSPACE_MODEL_SLOTS
    }
    target_is_default = bool(workspace.is_default_config)
    warnings: list[dict[str, Any]] = []

    if models_update is not None:
        mode_explicit = models_update.is_default_config is not None
        target_is_default = (
            models_update.is_default_config
            if mode_explicit
            else (
                False
                if any(
                    slot in models_update.model_fields_set
                    for slot in WORKSPACE_MODEL_SLOTS
                )
                else bool(workspace.is_default_config)
            )
        )
        if target_is_default:
            selection = workspace_service._default_model_values(db)
        else:
            selection = {
                slot: (
                    str(getattr(models_update, slot))
                    if getattr(models_update, slot) is not None
                    else None
                )
                for slot in WORKSPACE_MODEL_SLOTS
            }
            warnings = workspace_service._run_selection_gate(
                tenant_id=workspace.tenant_id,
                actor_id=actor.id,
                actor_name=actor.username,
                selection=selection,
                static_check=True,
                language=locale,
            )

    validation_slots = (
        WORKSPACE_MODEL_SLOTS
        if target_is_default
        else REQUIRED_WORKSPACE_MODEL_SLOTS
    )
    if not warnings:
        warnings = workspace_service._run_selection_gate(
            tenant_id=workspace.tenant_id,
            actor_id=actor.id,
            actor_name=actor.username,
            selection=selection,
            static_check=False,
            probe_slots=validation_slots,
            language=locale,
        )

    workspace_payload = _build_workspace_models_response(
        {
            **selection,
            "is_default_config": bool(target_is_default),
            "default_model_notice_pending": (
                workspace.default_model_notice_pending if target_is_default else False
            ),
        },
        locale=locale,
    )
    return {
        "workspace": workspace_payload,
        "valid": not bool(warnings),
        "warnings": warnings,
    }


def update_workspace_models(
    *,
    workspace_id: uuid.UUID,
    actor: User,
    models_update: WorkspaceModelsUpdate,
    locale: str,
) -> tuple[int, dict[str, Any]]:
    """Proxy the atomic write; the host re-runs its admin gate on the user id.

    ``exclude_unset`` preserves which slot keys the client actually sent: the
    host chooses default vs explicit mode from field presence.
    """

    payload = models_update.model_dump(mode="json", exclude_unset=True)
    return monolith.update_workspace_models(
        user_id=actor.id,
        tenant_id=actor.tenant_id,
        workspace_id=workspace_id,
        payload=payload,
        language=locale,
    )
