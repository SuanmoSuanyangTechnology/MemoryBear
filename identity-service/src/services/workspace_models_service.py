"""Workspace model configuration service for the W2-A public surface.

Identity owns the workspace rows but not the model tables: preset rendering
and the candidate catalog come from model-service. The workspace-model write
is identity's own (W2-B): this service commits the six slots and the mode
flag, then registers the rebuild job in memory-profile, which owns the model
resolution, the rebuild decision, the job row and the ``memory_config`` cache
invalidation. A refused registration (a non-2xx answer) rolls the columns back
conditionally, so a workspace never keeps a new embedding model without its
rebuild job; a transport failure instead keeps the write and the recovery
marker, because the job may already exist and a rollback would orphan it —
the replay converges the switch.

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
from sqlalchemy import update
from sqlalchemy.orm import Session

from src.core.exceptions import BusinessException
from src.core.response_utils import success
from src.i18n import translate
from src.integrations import memory_profile, model_service
from src.models.user_model import User
from src.models.workspace_model import Workspace
from src.repositories import management, workspace_repository
from src.schemas.workspace_schema import WorkspaceModelsResponse, WorkspaceModelsUpdate
from src.services import workspace_service, workspace_switch_recovery

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


def _resolve_update_target(
    db: Session,
    workspace: Workspace,
    models_update: WorkspaceModelsUpdate | None,
) -> tuple[bool, dict[str, Any]]:
    """The host's target resolution: returns (is_default, normalized selection)."""

    if models_update is None:
        return bool(workspace.is_default_config), {
            slot: getattr(workspace, slot) for slot in WORKSPACE_MODEL_SLOTS
        }
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
        return True, workspace_service._default_model_values(db)
    return False, {
        slot: (
            str(getattr(models_update, slot))
            if getattr(models_update, slot) is not None
            else None
        )
        for slot in WORKSPACE_MODEL_SLOTS
    }


def _validation_slots(target_is_default: bool) -> tuple[str, ...]:
    return (
        WORKSPACE_MODEL_SLOTS
        if target_is_default
        else REQUIRED_WORKSPACE_MODEL_SLOTS
    )


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
    tenant_id = workspace.tenant_id
    actor_id = actor.id
    # Close the read transaction before the outbound call: the lookup can
    # take its full read timeout and must not hold a pooled connection open.
    db.rollback()
    response["reembed_job_id"] = memory_profile.get_active_reembed_job_id(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=actor_id,
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
    target_is_default, selection = _resolve_update_target(
        db, workspace, models_update
    )
    tenant_id = workspace.tenant_id
    notice_pending = workspace.default_model_notice_pending
    actor_id = actor.id
    actor_name = actor.username
    # Close the read transaction before the gates; a probe can hold its full
    # read timeout, so no pooled connection may stay open across it.
    db.rollback()
    warnings: list[dict[str, Any]] = []

    if models_update is not None and not target_is_default:
        warnings = workspace_service._run_selection_gate(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name=actor_name,
            selection=selection,
            static_check=True,
            language=locale,
        )

    if not warnings:
        warnings = workspace_service._run_selection_gate(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name=actor_name,
            selection=selection,
            static_check=False,
            probe_slots=_validation_slots(target_is_default),
            language=locale,
        )

    workspace_payload = _build_workspace_models_response(
        {
            **selection,
            "is_default_config": bool(target_is_default),
            "default_model_notice_pending": (
                notice_pending if target_is_default else False
            ),
        },
        locale=locale,
    )
    return {
        "workspace": workspace_payload,
        "valid": not bool(warnings),
        "warnings": warnings,
    }


def _read_pre_image(db: Session, workspace_id: uuid.UUID) -> dict[str, Any]:
    """The current row values a failed registration rolls back to."""

    workspace = _workspace_or_not_found(db, workspace_id)
    return {
        **{slot: getattr(workspace, slot) for slot in WORKSPACE_MODEL_SLOTS},
        "is_default_config": workspace.is_default_config,
        "default_model_notice_pending": workspace.default_model_notice_pending,
    }


def _write_columns(
    db: Session,
    workspace_id: uuid.UUID,
    selection: dict[str, Any],
    *,
    target_is_default: bool,
) -> None:
    """Commit the switch's first half: the six slots and the mode flag."""

    db.execute(
        update(Workspace)
        .where(Workspace.id == workspace_id)
        .values(
            **{slot: selection.get(slot) for slot in WORKSPACE_MODEL_SLOTS},
            is_default_config=target_is_default,
            default_model_notice_pending=False,
        )
    )
    db.commit()


def _rollback_columns(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    written_embedding: str | None,
    pre_image: dict[str, Any],
) -> bool:
    """Restore the pre-image only while no concurrent switch owns the row.

    Returns whether the rollback ran without a database error — a row claimed
    by a concurrent switch (zero rows matched) also counts as resolved, since
    that writer is responsible for its own registration.
    """

    try:
        db.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id)
            .where(Workspace.embedding.is_not_distinct_from(written_embedding))
            .values(**pre_image)
        )
        db.commit()
        return True
    except Exception:
        logger.exception("模型切换回滚失败 workspace_id=%s", workspace_id)
        db.rollback()
        return False


def _update_success_result(
    selection: dict[str, Any],
    *,
    target_is_default: bool,
    job_id: Any,
    locale: str,
) -> dict[str, Any]:
    payload = _build_workspace_models_response(
        {
            **selection,
            "is_default_config": target_is_default,
            "default_model_notice_pending": False,
        },
        locale=locale,
    )
    payload["reembed_job_id"] = None if job_id is None else str(job_id)
    return success(
        data={"workspace": WorkspaceModelsResponse.model_validate(payload).model_dump()},
        msg=translate("workspace.models.config_updated", locale),
    )


def update_workspace_models(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor: User,
    models_update: WorkspaceModelsUpdate,
    locale: str,
) -> tuple[int, dict[str, Any]]:
    """Write the model columns, then register the rebuild job in memory-profile.

    Returns ``(status_code, envelope)``: a locally built success body, or the
    memory-profile failure forwarded verbatim after the columns were rolled
    back. Transport failures raise ``SERVICE_UNAVAILABLE``; with a recovery
    marker the write and marker stay so the replay converges, otherwise the
    columns are rolled back.
    """

    workspace = workspace_service._require_admin(db, workspace_id, actor)
    target_is_default, selection = _resolve_update_target(
        db, workspace, models_update
    )
    # Snapshot what the outbound calls need: the rollback expires the loaded
    # instances, and a lazy reload would reopen a transaction that must stay
    # closed across the gates and the registration call.
    tenant_id = workspace.tenant_id
    actor_id = actor.id
    actor_name = actor.username

    # End the read transaction before the selection gates (HTTP to model-service).
    db.rollback()
    if not target_is_default:
        issues = workspace_service._run_selection_gate(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name=actor_name,
            selection=selection,
            static_check=True,
            language=locale,
        )
        if issues:
            workspace_service._raise_model_config_error(issues, locale)
    issues = workspace_service._run_selection_gate(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=actor_name,
        selection=selection,
        static_check=False,
        probe_slots=_validation_slots(target_is_default),
        language=locale,
    )
    if issues:
        workspace_service._raise_model_config_error(issues, locale)

    # A switch stranded before its registration heals first: writing now would
    # overwrite the marker's pre-image and lose the intent to rebuild.
    pending = workspace_switch_recovery.replay_switch_for_workspace(workspace_id)
    if pending is not None:
        return pending

    pre_image = _read_pre_image(db, workspace_id)
    db.rollback()

    written_embedding = selection.get("embedding")
    marker = None
    if pre_image["embedding"] != written_embedding:
        marker = workspace_switch_recovery.mark_switch(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            user_id=actor_id,
            old_embedding_config_id=pre_image["embedding"],
            new_embedding_config_id=written_embedding,
        )

    try:
        _write_columns(
            db, workspace_id, selection, target_is_default=target_is_default
        )
    except Exception:
        # The marker stays: if the commit actually landed, the replay retries
        # the registration; otherwise it sees the old embedding and drops it.
        db.rollback()
        raise

    try:
        status, body = memory_profile.register_reembed_job(
            user_id=actor_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            old_embedding_config_id=pre_image["embedding"],
            new_embedding_config_id=written_embedding,
            idempotency_key=(
                None if marker is None else marker["idempotency_key"]
            ),
            language=locale,
        )
    except BusinessException:
        # The call's outcome is unknown: memory-profile may already have
        # committed the job and dispatched the rebuild. With a marker the
        # switch is replayable, so keep the write and the marker instead of
        # orphaning a job the rollback cannot see. Without one (same-slot
        # call, or the marker write failed) roll back as before.
        if marker is not None:
            logger.warning(
                "重算任务登记结果未知，保留模型列与自愈标记: workspace_id=%s",
                workspace_id,
            )
            raise
        if _rollback_columns(
            db,
            workspace_id,
            written_embedding=written_embedding,
            pre_image=pre_image,
        ):
            workspace_switch_recovery.clear_switch(workspace_id)
        raise

    if 200 <= status < 300 and body.get("code") == 0:
        workspace_switch_recovery.clear_switch(workspace_id)
        job_id = (body.get("data") or {}).get("job_id")
        logger.info(
            "工作空间模型配置更新成功: workspace_id=%s, embedding=%s, reembed_job_id=%s",
            workspace_id,
            written_embedding,
            job_id,
        )
        return 200, _update_success_result(
            selection,
            target_is_default=target_is_default,
            job_id=job_id,
            locale=locale,
        )

    if _rollback_columns(
        db,
        workspace_id,
        written_embedding=written_embedding,
        pre_image=pre_image,
    ):
        workspace_switch_recovery.clear_switch(workspace_id)
    logger.warning(
        "重算任务登记失败，已回滚模型列: workspace_id=%s, status=%s, code=%s",
        workspace_id,
        status,
        body.get("code"),
    )
    return status, body
