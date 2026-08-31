import hashlib
import secrets
import uuid
from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config.default_ontology_initializer import DefaultOntologyInitializer
from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.logging_config import get_business_logger
from app.core.utils.datetime_utils import utcnow_naive
from app.models.memory_config_model import MemoryConfig as MemoryConfigModel
from app.models.models_model import ModelCapability, ModelConfig, ModelProvider, ModelType
from app.models.user_model import User
from app.models.workspace_model import (
    InviteStatus,
    Workspace,
    WorkspaceDefaultModelPreset,
    WorkspaceMember,
    WorkspaceRole,
)
from app.repositories import workspace_repository
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.workspace_invite_repository import WorkspaceInviteRepository
from app.schemas.workspace_schema import (
    InviteAcceptRequest,
    InviteValidateResponse,
    WorkspaceCreate,
    WorkspaceInviteCreate,
    WorkspaceInviteResponse,
    WorkspaceMemberUpdate,
    WorkspaceModelsUpdate,
    WorkspaceUpdate,
)
from app.i18n import t
from app.invalidation_notify import notify_user_async, notify_user_sync
from app.services.memory_config_service import MemoryConfigService
from app.services.session_service import SessionService
from app.utils.redis_cache import (
    CACHE_MISS,
    get_json,
    get_workspace_model_public_version,
    set_json,
    workspace_model_options_key,
    invalidate_cache,
    invalidate_cache_sync,
)

# 获取业务逻辑专用日志器
business_logger = get_business_logger()

DEFAULT_PRESET_KEY = "default"
_WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", "vision", "audio", "video")
_REQUIRED_WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank")


def _serialize_model_option(model: ModelConfig) -> dict:
    return {
        "id": str(model.id),
        "name": model.name,
        "provider": getattr(model.provider, "value", model.provider),
        "type": getattr(model.type, "value", model.type),
        "capability": [getattr(item, "value", item) for item in (model.capability or [])],
        "logo": model.logo,
        "is_public": bool(model.is_public),
    }


def _get_accessible_workspace_models(db: Session, tenant_id: uuid.UUID) -> list[ModelConfig]:
    return (
        db.query(ModelConfig)
        .filter(ModelConfig.is_active.is_(True))
        .filter(
            or_(
                ModelConfig.tenant_id == tenant_id,
                (
                    (ModelConfig.provider == ModelProvider.SPEEDBEAR)
                    & ModelConfig.is_public.is_(True)
                ),
            )
        )
        .all()
    )


def _get_public_speedbear_models(db: Session) -> list[ModelConfig]:
    return (
        db.query(ModelConfig)
        .filter(ModelConfig.is_active.is_(True))
        .filter(ModelConfig.provider == ModelProvider.SPEEDBEAR)
        .filter(ModelConfig.is_public.is_(True))
        .all()
    )


def _slot_matches_model(slot: str, model: ModelConfig) -> bool:
    model_type = str(model.type)
    capability = set(model.capability or [])

    if slot == "llm":
        return model_type in {ModelType.LLM.value, ModelType.CHAT.value}
    if slot == "embedding":
        return model_type == ModelType.EMBEDDING.value
    if slot == "rerank":
        return model_type == ModelType.RERANK.value
    if slot == "vision":
        return ModelCapability.VISION.value in capability
    if slot == "audio":
        return ModelCapability.AUDIO.value in capability
    if slot == "video":
        return ModelCapability.VIDEO.value in capability
    return False


def _group_workspace_model_options(models: list[ModelConfig]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {slot: [] for slot in _WORKSPACE_MODEL_SLOTS}
    seen_ids = set()

    for model in models:
        model_id = str(model.id)
        if model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        data = _serialize_model_option(model)
        for slot in _WORKSPACE_MODEL_SLOTS:
            if _slot_matches_model(slot, model):
                grouped[slot].append(data)

    return grouped


def _get_default_workspace_preset(db: Session) -> WorkspaceDefaultModelPreset:
    preset = (
        db.query(WorkspaceDefaultModelPreset)
        .filter(WorkspaceDefaultModelPreset.singleton_key == DEFAULT_PRESET_KEY)
        .first()
    )
    if not preset:
        raise BusinessException("默认模型配置未设置", BizCode.CONFIG_MISSING)
    return preset


def _build_workspace_preset_response(db: Session, preset: WorkspaceDefaultModelPreset) -> dict:
    slot_to_model_id = {
        "llm": preset.llm_model_config_id,
        "embedding": preset.embedding_model_config_id,
        "rerank": preset.rerank_model_config_id,
        "vision": preset.vision_model_config_id,
        "audio": preset.audio_model_config_id,
        "video": preset.video_model_config_id,
    }
    model_ids = [model_id for model_id in slot_to_model_id.values() if model_id]
    models = (
        db.query(ModelConfig)
        .filter(ModelConfig.id.in_(model_ids))
        .all()
    )
    model_map = {model.id: model for model in models}
    result: dict[str, dict] = {}

    for slot, model_id in slot_to_model_id.items():
        model = model_map.get(model_id)
        if not model:
            raise BusinessException(f"默认模型配置缺少 {slot} 模型", BizCode.MODEL_NOT_FOUND)
        result[slot] = _serialize_model_option(model)

    return result


def _slot_label(slot: str, locale: str = "zh") -> str:
    """获取模型位的可读名称（如 llm -> 对话模型），缺少翻译时回退为原始 slot。"""
    key = f"workspace.models.slots.{slot}"
    label = t(key, locale=locale)
    return slot if label == key else label


def _display_model_id(model_id: uuid.UUID | str | None) -> str:
    """返回用于消息展示的短模型 ID（完整 UUID 会被敏感信息过滤器脱敏）。"""
    if not model_id:
        return "-"
    text = str(model_id)
    return text[:8] if len(text) > 8 else text


def _model_issue(
    slot: str,
    *,
    reason: str,
    locale: str = "zh",
    model_id: uuid.UUID | str | None = None,
    model_name: str | None = None,
    provider: str | None = None,
    model_type: str | None = None,
    detail: str | None = None,
) -> dict:
    """构造一条结构化的模型配置问题，供告警/错误详情返回给前端。

    Args:
        slot: 模型位（llm / embedding / rerank / vision / audio / video）
        reason: 问题原因码（not_configured / not_found / inactive / not_accessible /
            deprecated / capability_mismatch / no_api_key / api_verify_failed / verify_failed）
        locale: 语言代码（zh / en）
        model_id: 出问题的模型 ID
        model_name: 出问题的模型名称
        provider: 模型提供商
        model_type: 模型类型（用于能力不匹配提示）
        detail: 底层错误详情

    Returns:
        dict: 包含 slot / slot_label / model_id / model_name / reason / message 的问题详情
    """
    slot_label = _slot_label(slot, locale)
    display_name = model_name or (_display_model_id(model_id) if model_id else "-")
    message = t(
        f"workspace.models.errors.{reason}",
        locale=locale,
        slot=slot_label,
        model_id=_display_model_id(model_id) if model_id else "-",
        model_name=display_name,
        model_type=model_type or "-",
        error=detail or "-",
    )
    return {
        "slot": slot,
        # 兼容旧字段名，前端/调用方可继续使用 model_type 定位模型位
        "model_type": slot,
        "slot_label": slot_label,
        "model_id": str(model_id) if model_id else None,
        "model_name": model_name,
        "provider": provider,
        "reason": reason,
        "message": message,
        "detail": detail,
    }


def _issue_summary_message(issues: list[dict], locale: str = "zh") -> str:
    separator = "；" if str(locale).lower().startswith("zh") else "; "
    return t(
        "workspace.models.errors.save_failed",
        locale=locale,
        count=len(issues),
        reasons=separator.join(issue["message"] for issue in issues),
    )


def _raise_model_config_error(
    issues: list[dict],
    locale: str = "zh",
    *,
    code: BizCode = BizCode.INVALID_PARAMETER,
) -> None:
    """将模型配置问题聚合为一条 BusinessException 抛出，并携带结构化详情。"""
    raise BusinessException(
        _issue_summary_message(issues, locale),
        code,
        context={"error_details": {"errors": issues}},
    )


def _serialize_provider(model: ModelConfig) -> str | None:
    provider = getattr(model, "provider", None)
    return getattr(provider, "value", provider)


def _diagnose_unavailable_model(
    db: Session | None,
    slot: str,
    model_id: str,
    tenant_id: uuid.UUID | None,
    locale: str,
) -> dict:
    """诊断不在可选范围内的模型：不存在 / 已禁用 / 不属于当前租户 / 已弃用。"""
    model = None
    if db is not None:
        try:
            model = db.query(ModelConfig).filter(ModelConfig.id == uuid.UUID(str(model_id))).first()
        except Exception:  # 无效 UUID 或查询异常时按“不存在”处理
            model = None

    if model is None:
        return _model_issue(slot, reason="not_found", locale=locale, model_id=model_id)

    common = {
        "locale": locale,
        "model_id": model_id,
        "model_name": model.name,
        "provider": _serialize_provider(model),
        "model_type": str(getattr(model.type, "value", model.type)),
    }
    is_tenant_model = tenant_id is None or model.tenant_id == tenant_id
    is_public_speedbear = (
        model.provider == ModelProvider.SPEEDBEAR and bool(model.is_public)
    )
    if not (is_tenant_model or is_public_speedbear):
        # 跨租户模型只返回请求中的模型 ID，不泄露名称、供应商或状态。
        return _model_issue(slot, reason="not_accessible", locale=locale, model_id=model_id)
    if not model.is_active:
        return _model_issue(slot, reason="inactive", **common)
    if getattr(model, "model_base", None) is not None and getattr(model.model_base, "is_deprecated", False):
        return _model_issue(slot, reason="deprecated", **common)
    return _model_issue(slot, reason="not_accessible", **common)


def _collect_workspace_model_selection_issues(
    available_models: list[ModelConfig],
    selection: dict[str, uuid.UUID | str | None],
    *,
    require_all_slots: bool,
    locale: str = "zh",
    db: Session | None = None,
    tenant_id: uuid.UUID | None = None,
) -> tuple[dict[str, str | None], list[dict]]:
    """校验模型位与模型的匹配关系，收集全部问题而非遇错即停。"""
    model_map = {str(model.id): model for model in available_models}
    normalized: dict[str, str | None] = {}
    issues: list[dict] = []

    for slot in _WORKSPACE_MODEL_SLOTS:
        raw_value = selection.get(slot)
        if raw_value is None:
            if require_all_slots or slot in _REQUIRED_WORKSPACE_MODEL_SLOTS:
                issues.append(_model_issue(slot, reason="not_configured", locale=locale))
            normalized[slot] = None
            continue

        model_id = str(raw_value)
        model = model_map.get(model_id)
        if not model:
            issues.append(_diagnose_unavailable_model(db, slot, model_id, tenant_id, locale))
            normalized[slot] = None
            continue
        if not _slot_matches_model(slot, model):
            issues.append(
                _model_issue(
                    slot,
                    reason="capability_mismatch",
                    locale=locale,
                    model_id=model_id,
                    model_name=model.name,
                    provider=_serialize_provider(model),
                    model_type=str(getattr(model.type, "value", model.type)),
                )
            )
            normalized[slot] = None
            continue
        normalized[slot] = model_id

    return normalized, issues


def _validate_workspace_model_selection(
    available_models: list[ModelConfig],
    selection: dict[str, uuid.UUID | str | None],
    *,
    require_all_slots: bool,
    locale: str = "zh",
    db: Session | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict[str, str | None]:
    normalized, issues = _collect_workspace_model_selection_issues(
        available_models,
        selection,
        require_all_slots=require_all_slots,
        locale=locale,
        db=db,
        tenant_id=tenant_id,
    )
    if issues:
        business_logger.warning(f"工作空间模型选择校验失败: {[issue['message'] for issue in issues]}")
        _raise_model_config_error(issues, locale)
    return normalized


def _extract_workspace_model_values(source) -> dict[str, str | None]:
    getter = source.get if isinstance(source, dict) else lambda key: getattr(source, key, None)
    return {slot: getter(slot) for slot in _WORKSPACE_MODEL_SLOTS}


def _assign_workspace_models(workspace: Workspace, values: dict[str, str | None], *, is_default_config: bool) -> None:
    for slot, value in values.items():
        setattr(workspace, slot, value)
    workspace.is_default_config = is_default_config
    workspace.default_model_notice_pending = False


def _get_default_workspace_model_values(db: Session) -> dict[str, str]:
    preset = _get_default_workspace_preset(db)
    return {
        "llm": str(preset.llm_model_config_id),
        "embedding": str(preset.embedding_model_config_id),
        "rerank": str(preset.rerank_model_config_id),
        "vision": str(preset.vision_model_config_id),
        "audio": str(preset.audio_model_config_id),
        "video": str(preset.video_model_config_id),
    }


def _build_workspace_models_response(source, *, locale: str = "zh") -> dict:
    values = _extract_workspace_model_values(source)
    is_default_config = source.get("is_default_config") if isinstance(source, dict) else bool(source.is_default_config)
    notice_pending = (
        bool(source.get("default_model_notice_pending"))
        if isinstance(source, dict)
        else bool(getattr(source, "default_model_notice_pending", False))
    )
    response = {
        **values,
        "is_default_config": bool(is_default_config),
        "default_config_updated": notice_pending,
        "default_config_notice": (
            t("workspace.models.default_config_updated_notice", locale=locale)
            if notice_pending else None
        ),
    }
    return response


def _resolve_workspace_model_update_target(
    db: Session,
    workspace: Workspace,
    models_update: WorkspaceModelsUpdate | None,
    *,
    locale: str = "zh",
) -> tuple[bool, dict[str, str | None], tuple[str, ...], list[dict]]:
    """解析本次更新的目标配置。

    Returns:
        (是否使用默认配置, 归一化后的模型选择, 需要运行时校验的模型位, 静态校验问题列表)
    """
    selection = _extract_workspace_model_values(workspace)
    target_is_default = bool(workspace.is_default_config)
    issues: list[dict] = []

    if models_update:
        mode_explicit = models_update.is_default_config is not None
        target_is_default = (
            models_update.is_default_config
            if mode_explicit
            else (
                False if any(slot in models_update.model_fields_set for slot in _WORKSPACE_MODEL_SLOTS)
                else bool(workspace.is_default_config)
            )
        )
        if target_is_default:
            selection = _get_default_workspace_model_values(db)
        else:
            merged_selection = {
                slot: (
                    str(getattr(models_update, slot))
                    if getattr(models_update, slot) is not None
                    else None
                )
                for slot in _WORKSPACE_MODEL_SLOTS
            }
            selection, issues = _collect_workspace_model_selection_issues(
                _get_accessible_workspace_models(db, workspace.tenant_id),
                merged_selection,
                locale=locale,
                db=db,
                tenant_id=workspace.tenant_id,
                require_all_slots=False,
            )

    validation_slots = (
        _WORKSPACE_MODEL_SLOTS if target_is_default else _REQUIRED_WORKSPACE_MODEL_SLOTS
    )
    return target_is_default, selection, validation_slots, issues


def _invalidate_default_config_memory_caches(db: Session) -> None:
    """Invalidate Redis-cached memory configs of all default-config workspaces.

    Called after the default model preset changes so ``load_memory_config``
    re-resolves the new default models instead of returning stale cache.
    Invalidate every memory config under the affected workspaces (not just the
    default one), since they all resolve models from the preset.
    """
    workspace_ids = [
        row[0]
        for row in (
            db.query(Workspace.id)
            .filter(Workspace.is_active.is_(True))
            .filter(Workspace.is_default_config.is_(True))
            .all()
        )
    ]
    if not workspace_ids:
        return

    config_ids = (
        db.query(MemoryConfigModel.config_id)
        .filter(MemoryConfigModel.workspace_id.in_(workspace_ids))
        .all()
    )
    for (config_id,) in config_ids:
        try:
            invalidate_cache_sync(prefix=f"memory_config:{config_id}")
        except Exception:
            business_logger.warning(
                "Failed to invalidate memory_config cache for config=%s",
                config_id,
            )


_VALIDATE_AS_LLM_SLOTS = {"vision", "video", "audio", "image2text"}


async def _validate_workspace_slot_runtime(
    async_db: AsyncSession,
    slot: str,
    model_id: str,
    tenant_id: uuid.UUID | None,
    *,
    locale: str,
) -> dict | None:
    """校验单个模型位的运行时可用性，返回精确的问题详情（可用则返回 None）。"""
    from app.services.model_service import ModelApiKeyService
    from app.services.model_service import ModelConfigService as ModelSvc

    try:
        model_config = await ModelSvc.get_model_by_id_async(
            async_db, uuid.UUID(str(model_id)), tenant_id
        )
    except BusinessException as exc:
        reason = "deprecated" if exc.code == BizCode.MODEL_DEPRECATED else "not_found"
        return _model_issue(
            slot,
            reason=reason,
            locale=locale,
            model_id=model_id,
            detail=exc.message,
        )
    except Exception as exc:
        return _model_issue(
            slot,
            reason="not_found",
            locale=locale,
            model_id=model_id,
            detail=str(exc),
        )

    model_name = model_config.name
    provider = _serialize_provider(model_config)
    issue_context = {
        "locale": locale,
        "model_id": model_id,
        "model_name": model_name,
        "provider": provider,
        "model_type": str(getattr(model_config.type, "value", model_config.type)),
    }

    is_tenant_model = tenant_id is None or model_config.tenant_id == tenant_id
    is_public_speedbear = (
        model_config.provider == ModelProvider.SPEEDBEAR and bool(model_config.is_public)
    )
    if not (is_tenant_model or is_public_speedbear):
        # 跨租户模型只返回请求中的模型 ID，不泄露名称、供应商或状态。
        return _model_issue(
            slot,
            reason="not_accessible",
            locale=locale,
            model_id=model_id,
        )

    if not model_config.is_active:
        return _model_issue(slot, reason="inactive", **issue_context)

    if slot == "image2text":
        matches_slot = (
            str(model_config.type) in {ModelType.LLM.value, ModelType.CHAT.value}
            and ModelCapability.VISION.value in set(model_config.capability or [])
        )
    else:
        matches_slot = _slot_matches_model(slot, model_config)
    if not matches_slot:
        return _model_issue(slot, reason="capability_mismatch", **issue_context)

    try:
        api_key_config = await ModelApiKeyService.get_available_api_key_async(
            async_db, model_config.id, tenant_id
        )
    except BusinessException as exc:
        reason = "no_api_key" if exc.code == BizCode.AGENT_CONFIG_MISSING else "verify_failed"
        return _model_issue(
            slot,
            reason=reason,
            detail=exc.message,
            **issue_context,
        )
    except Exception as exc:
        return _model_issue(
            slot,
            reason="verify_failed",
            detail=str(exc),
            **issue_context,
        )

    if not api_key_config:
        return _model_issue(slot, reason="no_api_key", **issue_context)

    validate_type = "llm" if slot in _VALIDATE_AS_LLM_SLOTS else slot
    try:
        result = await ModelSvc.validate_model_config(
            async_db,
            model_name=api_key_config.model_name,
            provider=api_key_config.provider,
            api_key=api_key_config.api_key,
            api_base=api_key_config.api_base,
            model_type=validate_type,
            is_omni=api_key_config.is_omni,
            capability=api_key_config.capability,
        )
    except Exception as exc:
        return _model_issue(
            slot,
            reason="verify_failed",
            detail=str(exc),
            **issue_context,
        )

    if not result.get("valid"):
        return _model_issue(
            slot,
            reason="api_verify_failed",
            detail=str(result.get("error") or result.get("message") or "Unknown error"),
            **issue_context,
        )
    return None


async def validate_model_bindings_runtime_async(
    values: dict[str, str | None],
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    *,
    locale: str,
    slots_to_validate: tuple[str, ...],
) -> list[dict]:
    """校验最终绑定模型的可见性、能力、API Key 与连通性，并聚合问题。"""
    from app.db import get_async_db_context

    warnings: list[dict] = []
    async with get_async_db_context() as async_db:
        for slot in slots_to_validate:
            model_id = values.get(slot)
            if not model_id:
                warnings.append(_model_issue(slot, reason="not_configured", locale=locale))
                continue

            issue = await _validate_workspace_slot_runtime(
                async_db, slot, str(model_id), tenant_id, locale=locale
            )
            if issue is not None:
                business_logger.warning(
                    f"工作空间模型校验失败: workspace_id={workspace_id}, slot={slot}, "
                    f"model_id={model_id}, reason={issue['reason']}, detail={issue.get('detail')}"
                )
                warnings.append(issue)

    return warnings


async def _validate_workspace_model_runtime(
    db: Session,
    values: dict[str, str | None],
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    *,
    locale: str,
    slots_to_validate: tuple[str, ...],
) -> list[dict]:
    _ = db
    return await validate_model_bindings_runtime_async(
        values,
        tenant_id,
        workspace_id,
        locale=locale,
        slots_to_validate=slots_to_validate,
    )


def _resolve_workspace_create_payload(
    db: Session,
    workspace: WorkspaceCreate,
    tenant_id: uuid.UUID,
    *,
    locale: str = "zh",
) -> WorkspaceCreate:
    if workspace.is_default_config:
        return workspace.model_copy(update=_get_default_workspace_model_values(db))

    validated = _validate_workspace_model_selection(
        _get_accessible_workspace_models(db, tenant_id),
        {
            "llm": workspace.llm,
            "embedding": workspace.embedding,
            "rerank": workspace.rerank,
            "vision": workspace.vision,
            "audio": workspace.audio,
            "video": workspace.video,
        },
        require_all_slots=False,
        locale=locale,
        db=db,
        tenant_id=tenant_id,
    )
    return workspace.model_copy(update=validated)


def get_default_workspace_models(db: Session, *, allow_empty: bool = False) -> dict:
    try:
        preset = _get_default_workspace_preset(db)
    except BusinessException as exc:
        if allow_empty and exc.code == BizCode.CONFIG_MISSING:
            return {}
        raise
    return _build_workspace_preset_response(db, preset)


def update_default_workspace_models(db: Session, data, *, locale: str = "zh") -> dict:
    validated = _validate_workspace_model_selection(
        _get_public_speedbear_models(db),
        {
            "llm": data.llm,
            "embedding": data.embedding,
            "rerank": data.rerank,
            "vision": data.vision,
            "audio": data.audio,
            "video": data.video,
        },
        require_all_slots=True,
        locale=locale,
        db=db,
    )
    preset = (
        db.query(WorkspaceDefaultModelPreset)
        .filter(WorkspaceDefaultModelPreset.singleton_key == DEFAULT_PRESET_KEY)
        .first()
    )
    if not preset:
        preset = WorkspaceDefaultModelPreset(singleton_key=DEFAULT_PRESET_KEY)

    preset.llm_model_config_id = uuid.UUID(validated["llm"])
    preset.embedding_model_config_id = uuid.UUID(validated["embedding"])
    preset.rerank_model_config_id = uuid.UUID(validated["rerank"])
    preset.vision_model_config_id = uuid.UUID(validated["vision"])
    preset.audio_model_config_id = uuid.UUID(validated["audio"])
    preset.video_model_config_id = uuid.UUID(validated["video"])
    db.add(preset)
    db.commit()
    db.refresh(preset)
    _invalidate_default_config_memory_caches(db)
    return _build_workspace_preset_response(db, preset)


def get_workspace_model_options(db: Session, tenant_id: uuid.UUID) -> dict:
    public_version = get_workspace_model_public_version()
    cache_key = workspace_model_options_key(tenant_id, public_version)
    cached = get_json(cache_key)
    if cached is not CACHE_MISS and isinstance(cached, dict):
        return cached

    result = _group_workspace_model_options(_get_accessible_workspace_models(db, tenant_id))
    set_json(cache_key, result, 300)
    return result


def get_system_workspace_model_options(db: Session) -> dict:
    return _group_workspace_model_options(_get_public_speedbear_models(db))


def switch_workspace(
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
):
    """切换工作空间"""
    business_logger.debug(f"用户 {user.username} 请求切换工作空间为 {workspace_id}")

    # 检查用户是否为成员或超级管理员
    _check_workspace_member_permission(db, workspace_id, user)

    # 更新当前用户的工作空间上下文
    try:
        user.current_workspace_id = workspace_id
        db.commit()
        business_logger.info(f"用户 {user.username} 成功切换工作空间为 {workspace_id}")
        return
    except Exception as e:
        db.rollback()
        business_logger.error(f"切换工作空间失败 - 工作空间: {workspace_id}, 错误: {str(e)}")
        raise BusinessException(f"切换工作空间失败: {str(e)}", BizCode.INTERNAL_ERROR)


async def delete_workspace_member(
        db: Session,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
        user: User,
):
    """删除工作空间成员"""
    business_logger.debug(f"用户 {user.username} 请求删除工作空间 {workspace_id} 的成员 {member_id}")
    _check_workspace_admin_permission(db, workspace_id, user)
    workspace_member = workspace_repository.get_member_by_id(db=db, member_id=member_id)
    if not workspace_member:
        raise BusinessException(f"工作空间成员 {member_id} 不存在", BizCode.WORKSPACE_NOT_FOUND)

    if workspace_member.workspace_id != workspace_id:
        raise BusinessException(f"工作空间成员 {member_id} 不存在于工作空间 {workspace_id}",
                                BizCode.WORKSPACE_NOT_FOUND)

    try:
        deleted_user = workspace_member.user
        workspace_member.is_active = False
        deleted_user.current_workspace_id = None

        # 若被删除成员不是超级管理员且没有其他可用工作空间，则禁用该用户
        if not deleted_user.is_superuser:
            remaining = (
                db.query(WorkspaceMember)
                .filter(
                    WorkspaceMember.user_id == deleted_user.id,
                    WorkspaceMember.workspace_id != workspace_id,
                    WorkspaceMember.is_active.is_(True),
                )
                .count()
            )
            if remaining == 0:
                deleted_user.is_active = False

        db.commit()
        business_logger.info(f"用户 {user.username} 成功删除工作空间 {workspace_id} 的成员 {member_id}")

        # 使被删除成员的所有 token 立即失效
        await SessionService.invalidate_all_user_tokens(str(workspace_member.user_id))

        # 决策 #11 修订：workspace 成员变更发通知，identity 重建快照（workspace_id/roles 变化）
        await notify_user_async(str(workspace_member.user_id))
    except Exception as e:
        db.rollback()
        business_logger.error(f"删除工作空间成员失败 - 工作空间: {workspace_id}, 成员: {member_id}, 错误: {str(e)}")
        raise BusinessException(f"删除工作空间成员失败: {str(e)}", BizCode.INTERNAL_ERROR)


def get_user_workspaces(db: Session, user: User) -> List[Workspace]:
    """获取当前用户参与的所有工作空间
    
    For neo4j storage type workspaces, ensures each has a default memory config.
    If a workspace is missing a default config, one will be created automatically.
    
    Args:
        db: Database session
        user: Current user
        
    Returns:
        List[Workspace]: List of workspaces the user belongs to
    """
    business_logger.debug(f"获取用户工作空间列表: {user.username} (ID: {user.id})")
    workspaces = workspace_repository.get_workspaces_by_user(db=db, user_id=user.id)

    business_logger.info(f"用户 {user.username} 的工作空间数量: {len(workspaces)}")
    return workspaces


def _create_workspace_only(
        db: Session, workspace: WorkspaceCreate, owner: User
) -> Workspace:
    business_logger.debug(f"创建工作空间: {workspace.name}, 创建者: {owner.username}")

    try:
        # Create the workspace without adding any members
        business_logger.debug(f"创建工作空间: {workspace.name}")
        db_workspace = workspace_repository.create_workspace(
            db=db, workspace=workspace, tenant_id=owner.tenant_id
        )
        business_logger.info(f"工作空间创建成功: {db_workspace.name} (ID: {db_workspace.id}), 创建者: {owner.username}")
        return db_workspace
    except Exception as e:
        business_logger.error(f"创建工作空间失败: {workspace.name} - {str(e)}")
        raise


async def create_workspace(
        db: Session, workspace: WorkspaceCreate, user: User, language: str = "zh"
) -> Workspace:
    business_logger.info(
        f"创建工作空间: {workspace.name}, 创建者: {user.username}, "
        f"storage_type: {workspace.storage_type}"
    )
    if workspace_repository.get_workspaces_by_name(db=db, name=workspace.name, tenant_id=user.tenant_id):
        raise BusinessException(
            message="同名工作空间已存在",
            code=BizCode.RESOURCE_ALREADY_EXISTS
        )
    workspace = _resolve_workspace_create_payload(db, workspace, user.tenant_id, locale=language)

    selection = _extract_workspace_model_values(workspace)
    validation_slots = (
        _WORKSPACE_MODEL_SLOTS
        if workspace.is_default_config
        else _REQUIRED_WORKSPACE_MODEL_SLOTS
    )
    warnings = await _validate_workspace_model_runtime(
        db,
        selection,
        user.tenant_id,
        None,
        locale=language,
        slots_to_validate=validation_slots,
    )
    if warnings:
        _raise_model_config_error(warnings, language)

    llm = workspace.llm
    embedding = workspace.embedding
    rerank = workspace.rerank
    try:
        # Create the workspace without adding any members
        business_logger.debug(f"创建工作空间: {workspace.name}")
        db_workspace = workspace_repository.create_workspace(
            db=db, workspace=workspace, tenant_id=user.tenant_id
        )
        business_logger.info(f"工作空间创建成功: {db_workspace.name} (ID: {db_workspace.id}), 创建者: {user.username}")
        db.flush()  # 使用 flush 而不是 commit，获取 ID 但不提交事务
        db.refresh(db_workspace)

        # Initialize default ontology scenes for the workspace (先创建本体场景)
        default_scene_id = None
        default_scene_name = None
        try:
            initializer = DefaultOntologyInitializer(db)
            success, error_msg = initializer.initialize_default_scenes(
                db_workspace.id, language=language
            )

            if success:
                business_logger.info(
                    f"为工作空间 {db_workspace.id} 创建默认本体场景成功 (language={language})"
                )

                # 获取默认场景ID，优先使用"在线教育"场景，如果不存在则使用"情感陪伴"场景
                from app.repositories.ontology_scene_repository import OntologySceneRepository
                from app.config.default_ontology_config import (
                    ONLINE_EDUCATION_SCENE,
                    EMOTIONAL_COMPANION_SCENE,
                    get_scene_name
                )

                scene_repo = OntologySceneRepository(db)

                # 优先尝试获取教育场景
                education_scene_name = get_scene_name(ONLINE_EDUCATION_SCENE, language)
                education_scene = scene_repo.get_by_name(education_scene_name, db_workspace.id)

                if education_scene:
                    default_scene_id = education_scene.scene_id
                    default_scene_name = education_scene.scene_name
                    business_logger.info(
                        f"获取到教育场景ID用于默认记忆配置: {default_scene_id} (scene_name={education_scene_name})"
                    )
                else:
                    # 如果教育场景不存在，尝试获取情感陪伴场景
                    companion_scene_name = get_scene_name(EMOTIONAL_COMPANION_SCENE, language)
                    companion_scene = scene_repo.get_by_name(companion_scene_name, db_workspace.id)

                    if companion_scene:
                        default_scene_id = companion_scene.scene_id
                        default_scene_name = companion_scene.scene_name
                        business_logger.info(
                            f"教育场景不存在，使用情感陪伴场景ID用于默认记忆配置: {default_scene_id} (scene_name={companion_scene_name})"
                        )
                    else:
                        business_logger.warning(
                            f"未找到任何默认场景 (education={education_scene_name}, companion={companion_scene_name})"
                        )
            else:
                business_logger.warning(
                    f"为工作空间 {db_workspace.id} 创建默认本体场景失败: {error_msg} (language={language})"
                )
        except Exception as ontology_error:
            business_logger.error(
                f"为工作空间 {db_workspace.id} 创建默认本体场景异常: {str(ontology_error)} (language={language})"
            )
            # Don't fail workspace creation if default ontology initialization fails
            # The workspace can still function without default ontology scenes

        # 如果 storage_type 是 "rag"，自动创建知识库
        if workspace.storage_type == "rag":
            business_logger.info(
                f"检测到 storage_type 为 'rag'，开始为工作空间 "
                f"{db_workspace.id} 创建知识库"
            )
            try:
                from app.models.knowledge_model import KnowledgeType, PermissionType
                from app.repositories import knowledge_repository
                from app.schemas.knowledge_schema import KnowledgeCreate

                # 创建知识库数据
                knowledge_data = KnowledgeCreate(
                    workspace_id=db_workspace.id,
                    created_by=user.id,
                    parent_id=db_workspace.id,
                    name="USER_RAG_MERORY",
                    description=f"工作空间 {workspace.name} 的默认知识库",
                    avatar='',
                    type=KnowledgeType.General,
                    permission_id=PermissionType.Memory,
                    embedding_id=embedding,
                    reranker_id=rerank,
                    llm_id=llm,
                    image2text_id=llm,
                    parser_config={
                        "layout_recognize": "DeepDOC",
                        "chunk_token_num": 256,
                        "delimiter": "\n",
                        "auto_keywords": 0,
                        "auto_questions": 0,
                        "html4excel": False
                    }
                )

                # 直接使用 repository 创建知识库，避免 service 层的额外逻辑
                db_knowledge = knowledge_repository.create_knowledge(
                    db=db,
                    knowledge=knowledge_data
                )
                business_logger.info(
                    f"为工作空间 {db_workspace.id} 自动创建知识库成功: "
                    f"{db_knowledge.name} (ID: {db_knowledge.id})"
                )
            except Exception as kb_error:
                business_logger.error(
                    f"为工作空间 {db_workspace.id} 创建知识库失败: {str(kb_error)}"
                )
                db.rollback()
                raise BusinessException(
                    f"工作空间创建成功，但知识库创建失败: {str(kb_error)}",
                    BizCode.INTERNAL_ERROR
                )
        memory_config_service = MemoryConfigService(db)
        config_id = memory_config_service.create_workspace_default_config(
            db_workspace,
            default_scene_id,
            default_scene_name
        )
        db_workspace.memory_config = config_id
        db.flush()
        db.refresh(db_workspace)
        # 统一提交所有更改
        db.commit()
        business_logger.info(
            f"工作空间 {db_workspace.id} 及相关资源创建完成并已提交"
        )

        return db_workspace

    except Exception as e:
        business_logger.error(f"工作空间创建失败: {workspace.name} - {str(e)}")
        db.rollback()
        raise


def update_workspace(
        db: Session, workspace_id: uuid.UUID, workspace_in: WorkspaceUpdate, user: User
) -> Workspace:
    business_logger.info(f"更新工作空间: workspace_id={workspace_id}, 操作者: {user.username}")

    db_workspace = _check_workspace_admin_permission(db, workspace_id, user)
    try:
        # 更新工作空间
        business_logger.debug(f"执行工作空间更新: {db_workspace.name} (ID: {workspace_id})")
        update_data = workspace_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_workspace, field, value)

        db.add(db_workspace)
        db.commit()
        db.refresh(db_workspace)

        if "storage_type" in update_data:
            try:
                invalidate_cache_sync(prefix=f"storage_type:{workspace_id}")
            except Exception:
                pass

        if any(field in update_data for field in ("llm", "embedding", "rerank")) and db_workspace.memory_config:
            try:
                invalidate_cache_sync(prefix=f"memory_config:{db_workspace.memory_config}")
            except Exception:
                pass

        business_logger.info(f"工作空间更新成功: {db_workspace.name} (ID: {workspace_id})")
        return db_workspace
    except Exception as e:
        business_logger.error(f"工作空间更新失败: workspace_id={workspace_id} - {str(e)}")
        db.rollback()
        raise


def get_workspace_retention_policy(
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
) -> tuple[int | None, int]:
    """获取临时身份保留天数和至少有一条记忆的有效临时 EndUser 数量。"""
    _check_workspace_member_permission(db, workspace_id, user)
    retention_days = workspace_repository.get_workspace_retention_days(
        db=db,
        workspace_id=workspace_id,
    )
    end_user_count = (
        EndUserRepository(db).get_temporary_end_users_count_by_workspace(
            workspace_id
        )
    )
    return retention_days, end_user_count


def update_workspace_retention_policy(
        db: Session,
        workspace_id: uuid.UUID,
        retention_days: int | None,
        user: User,
) -> int | None:
    """以空间成员权限更新指定工作空间的临时身份保留天数。"""
    business_logger.info(
        f"更新工作空间保留策略: workspace_id={workspace_id}, "
        f"retention_days={retention_days}, 操作者={user.username}"
    )
    _check_workspace_member_permission(db, workspace_id, user)
    try:
        updated_retention_days = workspace_repository.update_workspace_retention_days(
            db=db,
            workspace_id=workspace_id,
            retention_days=retention_days,
        )
        business_logger.info(
            f"工作空间保留策略更新成功: workspace_id={workspace_id}, "
            f"retention_days={updated_retention_days}"
        )
        return updated_retention_days
    except Exception as e:
        business_logger.error(
            f"工作空间保留策略更新失败: workspace_id={workspace_id} - {str(e)}"
        )
        raise


def get_workspace_members(
        db: Session, workspace_id: uuid.UUID, user: User
) -> List[WorkspaceMember]:
    """获取某工作空间的成员列表（关系序列化由模型关系支持）"""
    business_logger.info(f"获取工作空间成员: workspace_id={workspace_id}, 操作者: {user.username}")

    # 查找工作空间
    business_logger.debug(f"查找工作空间: {workspace_id}")
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        business_logger.warning(f"工作空间不存在: {workspace_id}")
        raise BusinessException(
            message="Workspace not found",
            code=BizCode.WORKSPACE_NOT_FOUND
        )

    # 权限检查：工作空间成员或超级管理员可以查看成员列表
    from app.core.permissions import Action, Resource, Subject, permission_service
    member = workspace_repository.get_member_in_workspace(
        db=db, user_id=user.id, workspace_id=workspace_id
    )
    workspace_memberships = {workspace_id} if member else set()

    subject = Subject.from_user(user, workspace_memberships=workspace_memberships)
    resource = Resource.from_workspace(workspace)

    try:
        permission_service.require_permission(
            subject,
            Action.READ,
            resource,
            error_message=f"用户 {user.username} 没有查看工作空间 {workspace_id} 成员列表的权限"
        )
    except PermissionDeniedException as e:
        business_logger.warning(
            f"权限不足: 用户 {user.username} 尝试获取工作空间 {workspace_id} 成员列表"
        )
        raise BusinessException(str(e), BizCode.WORKSPACE_ACCESS_DENIED)

    # 查询成员并预加载 user/workspace 关系
    members = workspace_repository.get_members_by_workspace(db=db, workspace_id=workspace_id)
    business_logger.info(f"工作空间成员数量: {len(members)} - workspace_id={workspace_id}")
    return members


# ==================== 邀请相关服务方法 ====================

def _generate_invite_token() -> tuple[str, str]:
    """生成邀请令牌和其哈希值

    Returns:
        tuple: (原始令牌, 令牌哈希)
    """
    # 生成32字节的随机令牌
    token = secrets.token_urlsafe(32)
    # 生成令牌的SHA256哈希
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash


def _check_workspace_member_permission(db: Session, workspace_id: uuid.UUID, user: User) -> Workspace | None:
    """检查用户是否为工作空间成员或超级管理员（使用统一权限服务）"""
    # 获取工作空间信息
    db_workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not db_workspace:
        raise BusinessException(
            message="Workspace not found",
            code=BizCode.WORKSPACE_NOT_FOUND
        )

    # 使用统一权限服务检查访问权限
    from app.core.permissions import Action, Resource, Subject, permission_service

    # 获取用户的工作空间成员关系
    member = workspace_repository.get_member_in_workspace(
        db=db, user_id=user.id, workspace_id=workspace_id
    )

    # 任何成员都有访问权限
    workspace_memberships = {workspace_id} if member else set()

    subject = Subject.from_user(user, workspace_memberships=workspace_memberships)
    resource = Resource.from_workspace(db_workspace)

    try:
        permission_service.require_permission(
            subject,
            Action.READ,
            resource,
            error_message=f"用户 {user.username} 不是工作空间 {workspace_id} 的成员"
        )
        business_logger.debug(f"用户 {user.username} 是工作空间 {workspace_id} 的成员或超级管理员")
    except PermissionDeniedException as e:
        business_logger.warning(f"权限不足: 用户 {user.username} 尝试访问工作空间 {workspace_id}")
        raise BusinessException(str(e), BizCode.WORKSPACE_NO_ACCESS)
    return db_workspace


async def _check_workspace_member_permission_async(db: AsyncSession, workspace_id: uuid.UUID, user: User) -> Workspace | None:
    """Async version of _check_workspace_member_permission."""
    # 获取工作空间信息
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    db_workspace = result.scalars().first()
    if not db_workspace:
        raise BusinessException(message="Workspace not found", code=BizCode.WORKSPACE_NOT_FOUND)

    # 检查用户是否为工作空间成员
    member = await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user.id,
            WorkspaceMember.workspace_id == workspace_id,
        )
    )
    workspace_memberships = {workspace_id} if member else set()
    from app.core.permissions import Action, Resource, Subject, permission_service

    subject = Subject.from_user(user, workspace_memberships=workspace_memberships)
    resource = Resource.from_workspace(db_workspace)

    try:
        permission_service.require_permission(
            subject, Action.READ, resource,
            error_message=f"用户 {user.username} 不是工作空间 {workspace_id} 的成员",
        )
        business_logger.debug(f"用户 {user.username} 是工作空间 {workspace_id} 的成员或超级管理员")
    except PermissionDeniedException as e:
        business_logger.warning(f"权限不足: 用户 {user.username} 尝试访问工作空间 {workspace_id}")
        raise BusinessException(str(e), BizCode.WORKSPACE_NO_ACCESS)
    return db_workspace


def _check_workspace_admin_permission(db: Session, workspace_id: uuid.UUID, user: User) -> Workspace | None:
    """检查用户是否有工作空间管理员权限（使用统一权限服务）"""
    # 获取工作空间信息
    db_workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not db_workspace:
        raise BusinessException(
            message="Workspace not found",
            code=BizCode.WORKSPACE_NOT_FOUND
        )

    # 使用统一权限服务检查管理权限
    from app.core.permissions import Action, Resource, Subject, permission_service

    # 获取用户的工作空间成员关系
    member = workspace_repository.get_member_in_workspace(
        db=db, user_id=user.id, workspace_id=workspace_id
    )

    # 只有 manager 才有管理权限
    workspace_memberships = {workspace_id} if (member and member.role == WorkspaceRole.manager) else set()

    subject = Subject.from_user(user, workspace_memberships=workspace_memberships)
    resource = Resource.from_workspace(db_workspace)

    try:
        permission_service.require_permission(
            subject,
            Action.MANAGE,
            resource,
            error_message=f"用户 {user.username} 没有管理工作空间 {workspace_id} 的权限"
        )
        business_logger.debug(f"用户 {user.username} 有权限管理工作空间 {workspace_id}")
    except PermissionDeniedException as e:
        business_logger.warning(f"权限不足: 用户 {user.username} 尝试管理工作空间 {workspace_id}")
        raise BusinessException(str(e), BizCode.WORKSPACE_ACCESS_DENIED)
    return db_workspace


def create_workspace_invite(
        db: Session,
        workspace_id: uuid.UUID,
        invite_data: WorkspaceInviteCreate,
        user: User
) -> WorkspaceInviteResponse:
    """创建工作空间邀请"""
    business_logger.info(
        f"创建工作空间邀请: workspace_id={workspace_id}, email={invite_data.email}, 创建者: {user.username}")

    try:
        # 检查权限
        _check_workspace_admin_permission(db, workspace_id, user)
        # if settings.ENABLE_SINGLE_WORKSPACE:
        # 检查被邀请用户是否已经在工作空间中
        from app.repositories import user_repository
        invited_user = user_repository.get_user_by_email(db, invite_data.email)

        if invited_user:
            # 用户存在，检查是否已经是工作空间成员
            existing_member = workspace_repository.get_member_in_workspace(
                db=db,
                user_id=invited_user.id,
                workspace_id=workspace_id
            )
            if existing_member:
                business_logger.warning(f"用户 {invite_data.email} 已经是工作空间成员")
                raise BusinessException("该用户已经是工作空间成员", BizCode.RESOURCE_ALREADY_EXISTS)

        # 检查是否已有待处理的邀请
        invite_repo = WorkspaceInviteRepository(db)
        existing_invite = invite_repo.get_pending_invite_by_email_and_workspace(
            email=invite_data.email,
            workspace_id=workspace_id
        )

        invite_token = None
        if existing_invite:
            business_logger.info(f"邮箱 {invite_data.email} 在工作空间 {workspace_id} 已有待处理邀请，返回现有邀请")
            # 生成新的邀请链接（重新生成令牌）
            token, token_hash = _generate_invite_token()
            existing_invite.token_hash = token_hash
            existing_invite.updated_at = utcnow_naive()
            db.commit()
            db.refresh(existing_invite)
            invite_token = token
        else:
            # 生成邀请令牌
            token, token_hash = _generate_invite_token()
            # 创建邀请
            db_invite = invite_repo.create_invite(
                workspace_id=workspace_id,
                invite_data=invite_data,
                token_hash=token_hash,
                created_by_user_id=user.id
            )
            db.commit()
            db.refresh(db_invite)
            invite_token = token

        invite_obj = existing_invite or db_invite
        business_logger.info(f"工作空间邀请创建成功: invite_id={invite_obj.id}, email={invite_data.email}")

        # 构造响应
        response = WorkspaceInviteResponse.model_validate(invite_obj)
        response.invite_token = invite_token
        return response


    except Exception as e:
        db.rollback()
        business_logger.error(
            f"创建工作空间邀请失败: workspace_id={workspace_id}, email={invite_data.email} - {str(e)}")
        raise


def get_workspace_invites(
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
        status: Optional[InviteStatus] = None,
        limit: int = 50,
        offset: int = 0
) -> List[WorkspaceInviteResponse]:
    """获取工作空间邀请列表"""
    business_logger.info(f"获取工作空间邀请列表: workspace_id={workspace_id}, 操作者: {user.username}")

    # 检查工作空间是否存在
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        raise BusinessException("工作空间不存在", BizCode.WORKSPACE_NOT_FOUND)

    # 检查权限
    _check_workspace_admin_permission(db, workspace_id, user)

    # 获取邀请列表
    invite_repo = WorkspaceInviteRepository(db)
    invites = invite_repo.get_workspace_invites(
        workspace_id=workspace_id,
        status=status,
        limit=limit,
        offset=offset
    )

    return [WorkspaceInviteResponse.model_validate(invite) for invite in invites]


def validate_invite_token(db: Session, token: str) -> InviteValidateResponse:
    """验证邀请令牌"""
    business_logger.info("验证邀请令牌")

    # 生成令牌哈希
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # 查找邀请
    invite_repo = WorkspaceInviteRepository(db)
    invite = invite_repo.get_invite_by_token_hash(token_hash)

    if not invite:
        business_logger.warning("邀请令牌无效")
        raise BusinessException("邀请令牌无效", BizCode.WORKSPACE_INVITE_NOT_FOUND)

    # 检查邀请状态和过期时间
    now = utcnow_naive()
    is_expired = invite.expires_at < now or invite.status != InviteStatus.pending
    is_valid = not is_expired

    # 获取工作空间信息
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=invite.workspace_id)

    business_logger.info(f"邀请令牌验证完成: valid={is_valid}, expired={is_expired}")

    return InviteValidateResponse(
        workspace_name=workspace.name,
        workspace_id=invite.workspace_id,
        email=invite.email,
        role=WorkspaceRole(invite.role),
        is_expired=is_expired,
        is_valid=is_valid
    )


def accept_workspace_invite(
        db: Session,
        accept_request: InviteAcceptRequest,
        user: User
) -> dict:
    """接受工作空间邀请"""
    business_logger.info(f"接受工作空间邀请: 用户 {user.username}")

    try:
        from app.core.config import settings

        # 生成令牌哈希
        token_hash = hashlib.sha256(accept_request.token.encode()).hexdigest()

        # 查找邀请
        invite_repo = WorkspaceInviteRepository(db)
        invite = invite_repo.get_invite_by_token_hash(token_hash)

        if not invite:
            business_logger.warning("邀请令牌无效")
            raise BusinessException("邀请令牌无效", BizCode.WORKSPACE_INVITE_NOT_FOUND)

        # 检查邀请状态
        if invite.status != InviteStatus.pending:
            business_logger.warning(f"邀请已被处理: status={invite.status}")
            raise BusinessException(f"邀请已被{invite.status}", BizCode.WORKSPACE_INVITE_INVALID)

        # 检查过期时间
        now = utcnow_naive()
        if invite.expires_at < now:
            business_logger.warning("邀请已过期")
            # 标记为过期
            invite_repo.update_invite_status(invite.id, InviteStatus.expired)
            raise BusinessException("邀请已过期", BizCode.WORKSPACE_INVITE_EXPIRED)

        # 检查邮箱是否匹配
        if invite.email != user.email:
            business_logger.warning(f"邮箱不匹配: invite_email={invite.email}, user_email={user.email}")
            raise BusinessException("邮箱与邀请邮箱不匹配", BizCode.FORBIDDEN)

        # 如果启用单工作空间模式，检查用户是否已有工作空间
        if settings.ENABLE_SINGLE_WORKSPACE:
            user_workspaces = workspace_repository.get_workspaces_by_user(db=db, user_id=user.id)
            if user_workspaces:
                business_logger.warning(f"单工作空间模式下用户已有工作空间: user={user.username}")
                raise BusinessException("用户只能加入一个工作空间", BizCode.FORBIDDEN)

        # 检查用户是否已经是工作空间成员
        existing_member = workspace_repository.get_member_in_workspace(
            db=db,
            user_id=user.id,
            workspace_id=invite.workspace_id
        )

        if existing_member:
            business_logger.info("用户已是工作空间成员，更新邀请状态")
            invite_repo.update_invite_status(
                invite.id,
                InviteStatus.accepted,
                accepted_at=now
            )
            db.commit()
            workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=invite.workspace_id)
            return {
                "message": "You are already a member of this workspace",
                "workspace": workspace
            }

        # 将角色映射到工作空间角色（现在直接使用相同的角色）
        workspace_role = invite.role

        # 添加用户到工作空间
        workspace_repository.add_member_to_workspace(
            db=db,
            user_id=user.id,
            workspace_id=invite.workspace_id,
            role=workspace_role
        )

        # 标记邀请为已接受
        invite_repo.update_invite_status(
            invite.id,
            InviteStatus.accepted,
            accepted_at=now
        )

        db.commit()

        # 获取工作空间信息
        workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=invite.workspace_id)

        business_logger.info(
            f"用户成功加入工作空间: user={user.username}, workspace={workspace.name}, role={workspace_role}")

        # 决策 #11 修订：workspace 成员变更发通知，identity 重建快照（workspace_id/roles 变化）
        notify_user_sync(str(user.id))

        return {
            "message": "Successfully joined the workspace",
            "workspace": workspace,
            "role": workspace_role
        }

    except Exception as e:
        db.rollback()
        business_logger.error(f"接受工作空间邀请失败: user={user.username} - {str(e)}")
        raise


def revoke_workspace_invite(
        db: Session,
        workspace_id: uuid.UUID,
        invite_id: uuid.UUID,
        user: User
) -> dict:
    """撤销工作空间邀请"""
    business_logger.info(
        f"撤销工作空间邀请: workspace_id={workspace_id}, invite_id={invite_id}, 操作者: {user.username}")

    try:
        # 检查权限
        _check_workspace_admin_permission(db, workspace_id, user)

        # 撤销邀请
        invite_repo = WorkspaceInviteRepository(db)
        invite = invite_repo.revoke_invite(invite_id)

        if not invite:
            business_logger.warning(f"邀请不存在: invite_id={invite_id}")
            raise BusinessException("邀请不存在", BizCode.WORKSPACE_INVITE_NOT_FOUND)

        if invite.workspace_id != workspace_id:
            business_logger.warning(f"邀请不属于指定工作空间: invite_id={invite_id}, workspace_id={workspace_id}")
            raise BusinessException("邀请不属于指定工作空间", BizCode.BAD_REQUEST)

        db.commit()
        business_logger.info(f"工作空间邀请撤销成功: invite_id={invite_id}")
        return {"message": "邀请撤销成功"}

    except Exception as e:
        db.rollback()
        business_logger.error(f"撤销工作空间邀请失败: invite_id={invite_id} - {str(e)}")
        raise


def update_workspace_member_roles(
        db: Session,
        workspace_id: uuid.UUID,
        updates: List[WorkspaceMemberUpdate],
        user: User,
) -> List[WorkspaceMember]:
    """更新工作空间成员角色"""
    business_logger.info(
        f"更新工作空间成员角色: workspace_id={workspace_id}, 操作者: {user.username}, 更新数量: {len(updates)}")

    # 检查管理员权限
    _check_workspace_admin_permission(db, workspace_id, user)

    # 获取所有当前成员
    all_members = workspace_repository.get_members_by_workspace(db=db, workspace_id=workspace_id)
    member_map = {m.id: m for m in all_members}

    # 验证和业务规则检查
    update_ids = set()
    for upd in updates:
        # 检查成员是否存在
        if upd.id not in member_map:
            raise BusinessException(f"成员 {upd.id} 不存在于工作空间 {workspace_id}",
                                    BizCode.WORKSPACE_MEMBER_NOT_FOUND)

        member = member_map[upd.id]

        # 检查成员是否属于该工作空间
        if member.workspace_id != workspace_id:
            raise BusinessException(f"成员 {upd.id} 不属于工作空间 {workspace_id}", BizCode.WORKSPACE_MEMBER_NOT_FOUND)

        # 不能修改自己的角色
        if member.user_id == user.id:
            raise BusinessException("不能修改自己的角色", BizCode.BAD_REQUEST)

        update_ids.add(upd.id)

    # 检查是否至少保留一个 manager
    current_managers = [m for m in all_members if m.role == WorkspaceRole.manager]
    managers_after_update = [
        m for m in all_members
        if m.id not in update_ids and m.role == WorkspaceRole.manager
    ]

    # 添加更新后会成为 manager 的成员
    for upd in updates:
        if upd.role == WorkspaceRole.manager:
            managers_after_update.append(member_map[upd.id])

    if len(managers_after_update) == 0:
        raise BusinessException("工作空间至少需要一个管理员", BizCode.BAD_REQUEST)

    # 执行更新
    try:
        for upd in updates:
            workspace_repository.update_member_role_by_id(
                db=db,
                id=upd.id,
                role=upd.role,
            )
            business_logger.debug(f"更新成员 {upd.id} 角色为 {upd.role}")

        db.commit()

        # 重新获取更新后的成员列表
        updated_members = workspace_repository.get_members_by_workspace(db=db, workspace_id=workspace_id)
        business_logger.info(f"成员角色更新完成: workspace_id={workspace_id}, 更新数量={len(updates)}")

        # 决策 #11 修订：workspace 成员变更发通知，identity 重建快照（workspace_id/roles 变化）
        for upd in updates:
            notify_user_sync(str(member_map[upd.id].user_id))

        return updated_members

    except Exception as e:
        db.rollback()
        business_logger.error(f"更新工作空间成员角色失败: workspace_id={workspace_id} - {str(e)}")
        raise BusinessException(f"更新成员角色失败: {str(e)}", BizCode.INTERNAL_ERROR)


def get_workspace_storage_type(
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
) -> Optional[str]:
    """获取工作空间的存储类型

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        user: 当前用户

    Returns:
        storage_type: 存储类型字符串，如果未设置则返回 None
    """
    business_logger.info(f"用户 {user.username} 请求获取工作空间 {workspace_id} 的存储类型")

    # 检查用户是否有权限访问该工作空间
    _check_workspace_member_permission(db, workspace_id, user)

    # 查询工作空间
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(f"成功获取工作空间 {workspace_id} 的存储类型: {workspace.storage_type}")
    return workspace.storage_type


async def get_workspace_storage_type_async(
        db,
        workspace_id: uuid.UUID,
        user: User,
) -> Optional[str]:
    """Async version of get_workspace_storage_type."""
    from app.models.workspace_model import Workspace

    await _check_workspace_member_permission_async(db, workspace_id, user)

    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalars().first()
    if not workspace:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(code=BizCode.WORKSPACE_NOT_FOUND, message="工作空间不存在")

    business_logger.info(f"成功获取工作空间 {workspace_id} 的存储类型: {workspace.storage_type}")
    return workspace.storage_type


def get_workspace_storage_type_without_auth(
        db: Session,
        workspace_id: uuid.UUID,
) -> str:
    """获取工作空间的存储类型（无需权限验证，用于公开分享等场景）

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID

    Returns:
        storage_type: 存储类型字符串，如果未设置则返回 None
    """
    business_logger.info(f"获取工作空间 {workspace_id} 的存储类型（无权限验证）")

    # 查询工作空间
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(f"成功获取工作空间 {workspace_id} 的存储类型: {workspace.storage_type}")
    return workspace.storage_type


async def get_workspace_storage_type_without_auth_async(
        db: AsyncSession,
        workspace_id: uuid.UUID,
) -> str:
    """异步获取工作空间存储类型（无需权限验证，用于公开分享等场景）。"""
    from app.models.workspace_model import Workspace

    business_logger.info(f"获取工作空间 {workspace_id} 的存储类型（无权限验证，async）")
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalars().first()
    if not workspace:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(f"成功获取工作空间 {workspace_id} 的存储类型: {workspace.storage_type}")
    return workspace.storage_type


def get_workspace_models_configs(
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
        locale: str = "zh",
) -> Optional[dict]:
    """获取工作空间的模型配置（llm, embedding, rerank）

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        user: 当前用户
        locale: 语言代码（zh / en），用于 i18n 告警消息

    Returns:
        dict: 包含 llm, embedding, rerank 的字典，如果工作空间不存在则返回 None
    """
    business_logger.info(f"用户 {user.username} 请求获取工作空间 {workspace_id} 的模型配置")

    # 检查用户是否有权限访问该工作空间
    _check_workspace_member_permission(db, workspace_id, user)

    # 查询工作空间模型配置
    configs = workspace_repository.get_workspace_models_configs(db=db, workspace_id=workspace_id)

    if configs is None:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(
        f"成功获取工作空间 {workspace_id} 的模型配置: "
        f"llm={configs.get('llm')}, embedding={configs.get('embedding')}, rerank={configs.get('rerank')}"
    )
    return _build_workspace_models_response(configs, locale=locale)


async def get_workspace_models_configs_async(
        db: AsyncSession,
        workspace_id: uuid.UUID,
        user: User,
        locale: str = "zh",
) -> dict:
    """Async version of get_workspace_models_configs.

    Args:
        db: 异步数据库会话
        workspace_id: 工作空间ID
        user: 当前用户
        locale: 语言代码（zh / en），用于 i18n 告警消息

    Returns:
        dict: 包含 llm, embedding, rerank 的字典
    """
    from app.models.workspace_model import Workspace

    business_logger.info(f"用户 {user.username} 请求获取工作空间 {workspace_id} 的模型配置")

    await _check_workspace_member_permission_async(db, workspace_id, user)

    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalars().first()

    if workspace is None:
        business_logger.error(f"工作空间不存在: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在",
        )

    business_logger.info(
        f"成功获取工作空间 {workspace_id} 的模型配置: "
        f"llm={workspace.llm}, embedding={workspace.embedding}, rerank={workspace.rerank}"
    )
    return _build_workspace_models_response(workspace, locale=locale)


async def validate_workspace_models_configs(
        db: Session,
        workspace_id: uuid.UUID,
        user: User,
        locale: str = "zh",
        models_update: WorkspaceModelsUpdate | None = None,
) -> dict:
    db_workspace = _check_workspace_member_permission(db, workspace_id, user)
    target_is_default, selection, validation_slots, selection_issues = _resolve_workspace_model_update_target(
        db,
        db_workspace,
        models_update,
        locale=locale,
    )
    if selection_issues:
        # 选择本身不合法（模型不存在/已禁用/能力不匹配）时无需再做连通性校验
        warnings = selection_issues
    else:
        warnings = await _validate_workspace_model_runtime(
            db,
            selection,
            db_workspace.tenant_id,
            db_workspace.id,
            locale=locale,
            slots_to_validate=validation_slots,
        )
    workspace_payload = _build_workspace_models_response(
        {
            **selection,
            "is_default_config": bool(target_is_default),
            "default_model_notice_pending": (
                db_workspace.default_model_notice_pending if target_is_default else False
            ),
        },
        locale=locale,
    )
    return {
        "workspace": workspace_payload,
        "valid": not bool(warnings),
        "warnings": warnings,
    }


async def update_workspace_models_configs(
        db: Session,
        workspace_id: uuid.UUID,
        models_update: WorkspaceModelsUpdate,
        user: User,
        locale: str = "zh",
) -> dict:
    """更新工作空间的模型配置，并按模式执行阻断校验。

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        models_update: 模型配置更新对象
        user: 当前用户
        locale: 语言代码（zh / en），用于 i18n 告警消息

    Returns:
        dict: 更新后的工作空间配置
    """
    business_logger.info(f"用户 {user.username} 请求更新工作空间 {workspace_id} 的模型配置")

    # 检查用户是否有管理员权限
    db_workspace = _check_workspace_admin_permission(db, workspace_id, user)
    default_memory_config = MemoryConfigService(db).get_workspace_default_config(workspace_id=workspace_id)

    try:
        use_default_config, resolved_models, validation_slots, selection_issues = _resolve_workspace_model_update_target(
            db,
            db_workspace,
            models_update,
            locale=locale,
        )
        if selection_issues:
            _raise_model_config_error(selection_issues, locale)

        warnings = await _validate_workspace_model_runtime(
            db,
            resolved_models,
            db_workspace.tenant_id,
            db_workspace.id,
            locale=locale,
            slots_to_validate=validation_slots,
        )
        if warnings:
            _raise_model_config_error(warnings, locale)

        _assign_workspace_models(db_workspace, resolved_models, is_default_config=use_default_config)

        if default_memory_config:
            default_memory_config.llm_id = resolved_models["llm"]
            default_memory_config.reflection_model_id = resolved_models["llm"]
            default_memory_config.emotion_model_id = resolved_models["llm"]
            default_memory_config.embedding_id = resolved_models["embedding"]
            default_memory_config.rerank_id = resolved_models["rerank"]
            default_memory_config.vision_id = resolved_models["vision"]
            default_memory_config.audio_id = resolved_models["audio"]
            default_memory_config.video_id = resolved_models["video"]

        db.add(db_workspace)
        if default_memory_config:
            db.add(default_memory_config)
        db.commit()
        db.refresh(db_workspace)

        # Invalidate all cached memory configs under the workspace so the new
        # models take effect immediately (not just the default config).
        config_ids = (
            db.query(MemoryConfigModel.config_id)
            .filter(MemoryConfigModel.workspace_id == db_workspace.id)
            .all()
        )
        for (config_id,) in config_ids:
            try:
                await invalidate_cache(prefix=f"memory_config:{config_id}")
            except Exception:
                pass

        business_logger.info(
            f"工作空间模型配置更新成功: workspace_id={workspace_id}, "
            f"llm={db_workspace.llm}, embedding={db_workspace.embedding}, rerank={db_workspace.rerank}"
        )

        return _build_workspace_models_response(db_workspace, locale=locale)

    except BusinessException:
        db.rollback()
        raise
    except Exception as e:
        business_logger.error(f"工作空间模型配置更新失败: workspace_id={workspace_id} - {str(e)}")
        db.rollback()
        raise BusinessException(f"更新模型配置失败: {str(e)}", BizCode.INTERNAL_ERROR)
