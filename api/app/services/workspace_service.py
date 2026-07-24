import hashlib
import secrets
import uuid
from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config.default_ontology_initializer import DefaultOntologyInitializer
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.logging_config import get_business_logger
from app.core.utils.datetime_utils import utcnow_naive
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
from app.schemas.memory_config_schema import ConfigurationError
from app.i18n import t
from app.services.memory_config_service import MemoryConfigService
from app.services.session_service import SessionService

# 获取业务逻辑专用日志器
business_logger = get_business_logger()

_DEFAULT_PRESET_KEY = "default"
_WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", "vision", "audio", "video")
_REQUIRED_WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank")


def _serialize_model_option(model: ModelConfig) -> dict:
    return {
        "id": model.id,
        "name": model.name,
        "provider": model.provider,
        "type": model.type,
        "capability": list(model.capability or []),
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
        .filter(WorkspaceDefaultModelPreset.singleton_key == _DEFAULT_PRESET_KEY)
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


def _validate_workspace_model_selection(
    available_models: list[ModelConfig],
    selection: dict[str, uuid.UUID | str | None],
    *,
    require_all_slots: bool,
) -> dict[str, str | None]:
    model_map = {str(model.id): model for model in available_models}
    normalized: dict[str, str | None] = {}

    for slot in _WORKSPACE_MODEL_SLOTS:
        raw_value = selection.get(slot)
        if raw_value is None:
            if require_all_slots or slot in _REQUIRED_WORKSPACE_MODEL_SLOTS:
                raise BusinessException(f"{slot} 模型未配置", BizCode.INVALID_PARAMETER)
            normalized[slot] = None
            continue

        model_id = str(raw_value)
        model = model_map.get(model_id)
        if not model:
            raise BusinessException(f"{slot} 模型不存在或不可用", BizCode.MODEL_NOT_FOUND)
        if not _slot_matches_model(slot, model):
            raise BusinessException(f"{slot} 模型能力不匹配", BizCode.INVALID_PARAMETER)
        normalized[slot] = model_id

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
) -> tuple[bool, dict[str, str | None], tuple[str, ...]]:
    selection = _extract_workspace_model_values(workspace)
    target_is_default = bool(workspace.is_default_config)

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
            selection = _validate_workspace_model_selection(
                _get_accessible_workspace_models(db, workspace.tenant_id),
                merged_selection,
                require_all_slots=False,
            )

    validation_slots = (
        _WORKSPACE_MODEL_SLOTS if target_is_default else _REQUIRED_WORKSPACE_MODEL_SLOTS
    )
    return target_is_default, selection, validation_slots


def _sync_workspace_default_memory_config(workspace: Workspace, memory_config) -> None:
    if not memory_config:
        return

    memory_config.llm_id = workspace.llm
    memory_config.reflection_model_id = workspace.llm
    memory_config.emotion_model_id = workspace.llm
    memory_config.embedding_id = workspace.embedding
    memory_config.rerank_id = workspace.rerank
    memory_config.vision_id = workspace.vision
    memory_config.audio_id = workspace.audio
    memory_config.video_id = workspace.video


def _sync_default_config_workspaces(
    db: Session,
    resolved_models: dict[str, str | None],
) -> None:
    workspaces = (
        db.query(Workspace)
        .filter(Workspace.is_active.is_(True))
        .filter(Workspace.is_default_config.is_(True))
        .all()
    )
    if not workspaces:
        return

    memory_config_service = MemoryConfigService(db)
    for workspace in workspaces:
        for slot, value in resolved_models.items():
            setattr(workspace, slot, value)
        workspace.default_model_notice_pending = True
        default_memory_config = memory_config_service.get_workspace_default_config(workspace.id)
        _sync_workspace_default_memory_config(workspace, default_memory_config)


async def _validate_workspace_model_runtime(
    db: Session,
    values: dict[str, str | None],
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    locale: str,
    slots_to_validate: tuple[str, ...],
) -> list[dict]:
    service = MemoryConfigService(db)
    warnings: list[dict] = []
    validate_as_llm = {"vision", "video", "audio"}

    async def _validate_one(model_type: str, model_id: str) -> dict | None:
        validate_type = "llm" if model_type in validate_as_llm else model_type
        try:
            await service._validate_model_connectivity(
                model_id,
                validate_type,
                tenant_id,
                None,
                workspace_id,
                locale=locale,
            )
            return None
        except ConfigurationError as exc:
            return {
                "model_type": model_type,
                "model_id": str(model_id),
                "message": exc.err_message,
            }

    for slot in slots_to_validate:
        if not values.get(slot):
            warnings.append({
                "model_type": slot,
                "model_id": None,
                "message": t("memory_config.model.not_configured", locale=locale, model_type=slot),
            })

    for slot in slots_to_validate:
        model_id = values.get(slot)
        if not model_id:
            continue
        result = await _validate_one(slot, model_id)
        if result is not None:
            warnings.append(result)

    return warnings


def _resolve_workspace_create_payload(db: Session, workspace: WorkspaceCreate, tenant_id: uuid.UUID) -> WorkspaceCreate:
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


def update_default_workspace_models(db: Session, data) -> dict:
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
    )
    preset = (
        db.query(WorkspaceDefaultModelPreset)
        .filter(WorkspaceDefaultModelPreset.singleton_key == _DEFAULT_PRESET_KEY)
        .first()
    )
    if not preset:
        preset = WorkspaceDefaultModelPreset(singleton_key=_DEFAULT_PRESET_KEY)

    preset.llm_model_config_id = uuid.UUID(validated["llm"])
    preset.embedding_model_config_id = uuid.UUID(validated["embedding"])
    preset.rerank_model_config_id = uuid.UUID(validated["rerank"])
    preset.vision_model_config_id = uuid.UUID(validated["vision"])
    preset.audio_model_config_id = uuid.UUID(validated["audio"])
    preset.video_model_config_id = uuid.UUID(validated["video"])
    _sync_default_config_workspaces(db, validated)
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return _build_workspace_preset_response(db, preset)


def get_workspace_model_options(db: Session, tenant_id: uuid.UUID) -> dict:
    return _group_workspace_model_options(_get_accessible_workspace_models(db, tenant_id))


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
    workspace = _resolve_workspace_create_payload(db, workspace, user.tenant_id)

    validation_slots = _WORKSPACE_MODEL_SLOTS if workspace.is_default_config else _REQUIRED_WORKSPACE_MODEL_SLOTS
    selection = _extract_workspace_model_values(workspace)
    warnings = await _validate_workspace_model_runtime(
        db,
        selection,
        user.tenant_id,
        None,
        locale=language,
        slots_to_validate=validation_slots,
    )
    if warnings:
        raise BusinessException(warnings[0]["message"], BizCode.INVALID_PARAMETER)

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
        business_logger.info(f"工作空间更新成功: {db_workspace.name} (ID: {workspace_id})")
        return db_workspace
    except Exception as e:
        business_logger.error(f"工作空间更新失败: workspace_id={workspace_id} - {str(e)}")
        db.rollback()
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
    target_is_default, selection, validation_slots = _resolve_workspace_model_update_target(
        db,
        db_workspace,
        models_update,
    )
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
        use_default_config, resolved_models, validation_slots = _resolve_workspace_model_update_target(
            db,
            db_workspace,
            models_update,
        )
        warnings = await _validate_workspace_model_runtime(
            db,
            resolved_models,
            db_workspace.tenant_id,
            db_workspace.id,
            locale=locale,
            slots_to_validate=validation_slots,
        )
        if warnings:
            raise BusinessException(warnings[0]["message"], BizCode.INVALID_PARAMETER)

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
        if default_memory_config:
            db.refresh(default_memory_config)

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
