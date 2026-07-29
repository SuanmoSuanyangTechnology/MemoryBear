import copy
import uuid
import io
import json
import time
from dataclasses import dataclass
from typing import Optional, Annotated

import yaml
from fastapi import APIRouter, Depends, Path, Form, UploadFile, File, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload
from urllib.parse import quote

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success, fail
from app.db import get_db, get_async_db_context
from app.dependencies import get_current_user, get_current_user_async, cur_workspace_access_guard
from app.models import User
from app.models.annotation_model import HitLogSource
from app.models.app_model import AppType
from app.repositories import knowledge_repository
from app.schemas import app_schema, tool_schema
from app.schemas.response_schema import PageData, PageMeta
from app.schemas.workflow_schema import WorkflowConfig as WorkflowConfigSchema
from app.schemas.workflow_schema import WorkflowConfigUpdate, WorkflowImportSave
from app.services import app_service, workspace_service
from app.services.agent_config_helper import enrich_agent_config
from app.services.app_service import AppService
from app.services.app_statistics_service import AppStatisticsService
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.services.workflow_import_service import WorkflowImportService
from app.services.workflow_service import WorkflowService, get_workflow_service
from app.services.app_dsl_service import AppDslService
from app.core.quota_stub import check_app_quota
from app.utils.redis_cache import delete_json_async, workflow_config_key

router = APIRouter(prefix="/apps", tags=["Apps"])
logger = get_business_logger()


@dataclass(frozen=True)
class _DraftRunAppSnapshot:
    id: uuid.UUID
    workspace_id: uuid.UUID
    type: str
    current_release_id: uuid.UUID | None


def _build_agent_config_snapshot(source):
    from app.models import AgentConfig
    return _build_agent_config(
        AgentConfig,
        id=source.id,
        app_id=source.app_id,
        system_prompt=source.system_prompt,
        default_model_config_id=source.default_model_config_id,
        model_parameters=copy.deepcopy(source.model_parameters),
        knowledge_retrieval=copy.deepcopy(source.knowledge_retrieval),
        memory=copy.deepcopy(source.memory),
        variables=copy.deepcopy(source.variables),
        tools=copy.deepcopy(source.tools),
        skills=copy.deepcopy(source.skills),
        features=copy.deepcopy(source.features),
        agent_role=source.agent_role,
        agent_domain=source.agent_domain,
        parent_agent_id=source.parent_agent_id,
        capabilities=copy.deepcopy(source.capabilities),
        is_active=source.is_active,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _build_agent_config(agent_config_cls, **kwargs):
    return agent_config_cls(**kwargs)


def _build_agent_config_from_release(*, release, real_config_id, created_at):
    from app.models import AgentConfig

    cfg = release.config or {}
    return _build_agent_config(
        AgentConfig,
        id=real_config_id or uuid.uuid4(),
        app_id=release.app_id,
        system_prompt=cfg.get("system_prompt", ""),
        default_model_config_id=release.default_model_config_id,
        model_parameters=copy.deepcopy(cfg.get("model_parameters")),
        knowledge_retrieval=copy.deepcopy(cfg.get("knowledge_retrieval")),
        memory=copy.deepcopy(cfg.get("memory", {})),
        variables=copy.deepcopy(cfg.get("variables", [])),
        tools=copy.deepcopy(cfg.get("tools", [])),
        skills=copy.deepcopy(cfg.get("skills", {})),
        features=copy.deepcopy(cfg.get("features", {})),
        is_active=True,
        created_at=created_at,
        updated_at=created_at,
    )


def _build_model_config_snapshot(source):
    from app.models import ModelConfig

    snapshot = ModelConfig(
        id=source.id,
        model_id=source.model_id,
        tenant_id=source.tenant_id,
        logo=source.logo,
        name=source.name,
        provider=source.provider,
        type=source.type,
        is_composite=source.is_composite,
        description=source.description,
        capability=copy.deepcopy(source.capability),
        is_omni=source.is_omni,
        config=copy.deepcopy(source.config),
        is_public=source.is_public,
        load_balance_strategy=source.load_balance_strategy,
        created_at=source.created_at,
        updated_at=source.updated_at,
        is_active=source.is_active,
    )
    if hasattr(source, "api_keys") and source.api_keys is not None:
        snapshot.api_keys = source.api_keys
    return snapshot


async def _load_draft_run_app_snapshot(
        app_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
) -> _DraftRunAppSnapshot:
    from app.models import App, AppShare

    async with get_async_db_context() as db:
        app = await db.scalar(
            select(App).where(App.id == app_id).limit(1)
        )
        if not app:
            raise BusinessException("应用不存在", BizCode.NOT_FOUND)

        if workspace_id is not None and app.workspace_id != workspace_id:
            share_exists = await db.scalar(
                select(AppShare.id).where(
                    AppShare.source_app_id == app.id,
                    AppShare.target_workspace_id == workspace_id,
                    AppShare.is_active.is_(True),
                ).limit(1)
            )
            if not share_exists:
                raise BusinessException("应用不可访问", BizCode.WORKSPACE_NO_ACCESS)

        return _DraftRunAppSnapshot(
            id=app.id,
            workspace_id=app.workspace_id,
            type=app.type,
            current_release_id=app.current_release_id,
        )


async def _get_or_create_draft_end_user_id(
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        other_id: str,
) -> str:
    from app.models import EndUser, EndUserInfo

    async with get_async_db_context() as db:
        existing_end_user = await db.scalar(
            select(EndUser).where(
                EndUser.workspace_id == workspace_id,
                EndUser.other_id == other_id,
                EndUser.is_active.is_(True),
            ).order_by(EndUser.created_at.asc()).limit(1)
        )
        if existing_end_user and existing_end_user.app_id == app_id:
            return str(existing_end_user.id)

        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{workspace_id}|{other_id}"},
        )
        end_user = await db.scalar(
            select(EndUser).where(
                EndUser.workspace_id == workspace_id,
                EndUser.other_id == other_id,
                EndUser.is_active.is_(True),
            ).order_by(EndUser.created_at.asc()).limit(1)
        )
        if end_user:
            end_user.app_id = app_id
            await db.commit()
            return str(end_user.id)

        end_user = EndUser(
            app_id=app_id,
            workspace_id=workspace_id,
            other_id=other_id,
        )
        db.add(end_user)
        await db.flush()
        db.add(
            EndUserInfo(
                end_user_id=end_user.id,
                other_name="",
                aliases=[],
                meta_data={},
            )
        )
        await db.commit()
        return str(end_user.id)


async def _ensure_workflow_conversation_ready(
        *,
        conversation_id: Optional[str],
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
) -> Optional[str]:
    if not conversation_id:
        return conversation_id

    from app.models import AppShare, Conversation

    try:
        conv_uuid = uuid.UUID(conversation_id)
    except Exception as exc:
        raise BusinessException(
            f"会话不存在: {conversation_id}",
            BizCode.NOT_FOUND,
            cause=exc,
        ) from exc

    async with get_async_db_context() as db:
        conversation = await db.get(Conversation, conv_uuid)
        if not conversation:
            raise BusinessException(f"会话不存在: {conversation_id}", BizCode.NOT_FOUND)

        if conversation.workspace_id != workspace_id:
            share_exists = await db.scalar(
                select(AppShare.id).where(
                    AppShare.source_app_id == app_id,
                    AppShare.target_workspace_id == workspace_id,
                    AppShare.is_active.is_(True),
                ).limit(1)
            )
            same_app = conversation.app_id == app_id
            if not share_exists and not same_app:
                raise BusinessException("会话不属于当前工作空间", BizCode.PERMISSION_DENIED)

    return conversation_id


async def _load_workflow_runtime_config(
        *,
        app_id: uuid.UUID,
        app_workspace_id: uuid.UUID,
        current_release_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
) -> tuple[object, bool]:
    from app.core.utils.datetime_utils import utcnow_naive
    from app.core.workflow.validator import validate_workflow_config
    from app.models import AppRelease, WorkflowConfig

    is_shared = app_workspace_id != workspace_id
    workflow_service = WorkflowService()

    if is_shared:
        if not current_release_id:
            raise BusinessException("该应用尚未发布，无法使用", BizCode.AGENT_CONFIG_MISSING)

        async with get_async_db_context() as db:
            release = await db.get(AppRelease, current_release_id)
            if not release:
                raise BusinessException("发布版本不存在", BizCode.AGENT_CONFIG_MISSING)
            real_config_id = await db.scalar(
                select(WorkflowConfig.id).where(
                    WorkflowConfig.app_id == release.app_id,
                    WorkflowConfig.is_active.is_(True),
                ).limit(1)
            )
            cfg = release.config or {}
            now = release.created_at or utcnow_naive()

        return WorkflowConfig(
            id=real_config_id or uuid.uuid4(),
            app_id=app_id,
            nodes=cfg.get("nodes", []),
            edges=cfg.get("edges", []),
            variables=cfg.get("variables", []),
            environment_variables=cfg.get("environment_variables") or [],
            execution_config=cfg.get("execution_config", {}),
            triggers=cfg.get("triggers", []),
            features=cfg.get("features", {}),
            workflow_type=workflow_service._normalize_workflow_type(cfg.get("workflow_type")),
            is_active=True,
            created_at=now,
            updated_at=now,
        ), True

    config = await workflow_service.get_workflow_config_snapshot_async(app_id)
    if not config:
        raise BusinessException("工作流配置不存在，无法运行", BizCode.CONFIG_MISSING)

    config_id = config.id
    config_app_id = config.app_id
    config_is_active = config.is_active
    config_created_at = config.created_at
    config_updated_at = config.updated_at
    config_dict = workflow_service._prepare_workflow_config_dict(
        nodes=config.nodes,
        edges=config.edges,
        variables=config.variables,
        environment_variables=config.environment_variables,
        execution_config=config.execution_config,
        features=config.features,
        triggers=config.triggers,
        workflow_type=config.workflow_type,
    )

    is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
    if not is_valid:
        raise BusinessException(
            code=BizCode.INVALID_PARAMETER,
            message=f"工作流配置无效: {'; '.join(errors)}",
        )

    return WorkflowConfig(
        id=config_id,
        app_id=config_app_id,
        nodes=config_dict["nodes"],
        edges=config_dict["edges"],
        variables=config_dict["variables"],
        environment_variables=config_dict["environment_variables"],
        execution_config=config_dict["execution_config"],
        features=config_dict["features"],
        triggers=config_dict["triggers"],
        workflow_type=config_dict["workflow_type"],
        is_active=config_is_active,
        created_at=config_created_at,
        updated_at=config_updated_at,
    ), False


async def _prepare_draft_memory_context_async(
        *,
        workspace_id: uuid.UUID,
        current_user,
) -> tuple[str, str]:
    async with get_async_db_context() as db:
        storage_type = await workspace_service.get_workspace_storage_type_async(
            db=db,
            workspace_id=workspace_id,
            user=current_user,
        )
        if storage_type is None:
            storage_type = "neo4j"

        user_rag_memory_id = ""
        knowledge = await knowledge_repository.get_knowledge_by_name_async(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id,
        )
        if knowledge:
            user_rag_memory_id = str(knowledge.id)

    return storage_type, user_rag_memory_id


async def _load_agent_runtime_config(
        *,
        app_id: uuid.UUID,
        app_workspace_id: uuid.UUID,
        current_release_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
):
    from app.core.exceptions import ResourceNotFoundException
    from app.core.utils.datetime_utils import utcnow_naive
    from app.models import AgentConfig, AppRelease, ModelConfig

    is_shared = app_workspace_id != workspace_id

    async with get_async_db_context() as db:
        if is_shared:
            if not current_release_id:
                raise BusinessException("该应用尚未发布，无法使用", BizCode.AGENT_CONFIG_MISSING)

            release = await db.get(AppRelease, current_release_id)
            if not release:
                raise BusinessException("发布版本不存在", BizCode.AGENT_CONFIG_MISSING)

            real_config = await db.scalar(
                select(AgentConfig).where(AgentConfig.app_id == release.app_id).limit(1)
            )
            now = release.created_at or utcnow_naive()
            agent_cfg_snapshot = _build_agent_config_from_release(
                release=release,
                real_config_id=real_config.id if real_config else None,
                created_at=now,
            )
        else:
            agent_cfg = await db.scalar(
                select(AgentConfig).where(AgentConfig.app_id == app_id).limit(1)
            )
            if not agent_cfg:
                raise BusinessException("Agent 配置不存在", BizCode.AGENT_CONFIG_MISSING)
            agent_cfg_snapshot = _build_agent_config_snapshot(agent_cfg)

        model_config = None
        if agent_cfg_snapshot.default_model_config_id:
            model_config = await db.scalar(
                select(ModelConfig)
                .where(ModelConfig.id == agent_cfg_snapshot.default_model_config_id)
                .options(selectinload(ModelConfig.api_keys))
            )
            if not model_config:
                raise ResourceNotFoundException("模型配置", str(agent_cfg_snapshot.default_model_config_id))

        if not model_config:
            raise BusinessException("模型配置不存在，无法试运行", BizCode.AGENT_CONFIG_MISSING)

        model_config_snapshot = _build_model_config_snapshot(model_config)

        # SpeedBear 公共模型的 api_key 来自 TenantSpeedBearBinding，不存于 api_keys 关系
        if model_config_snapshot.provider == "speedbear" and model_config_snapshot.is_public:
            from app.models.workspace_model import Workspace
            tenant_row = await db.execute(
                select(Workspace.tenant_id).where(Workspace.id == workspace_id).limit(1)
            )
            tenant_id = tenant_row.scalar_one_or_none()
            if tenant_id:
                from premium.platform_admin.speedbear_model import TenantSpeedBearBinding
                bind_row = await db.execute(
                    select(TenantSpeedBearBinding)
                    .where(TenantSpeedBearBinding.tenant_id == tenant_id)
                    .limit(1)
                )
                binding = bind_row.scalar_one_or_none()
                if binding:
                    from app.models.models_model import ModelApiKey
                    base_url = f"{settings.SPEEDBEAR_BASE_URL.rstrip('/')}/api/v1"
                    model_config_snapshot.api_keys = [
                        ModelApiKey(
                            model_name=model_config_snapshot.name,
                            provider="speedbear",
                            api_key=binding.gateway_api_key,
                            api_base=base_url,
                            capability=model_config_snapshot.capability,
                            is_omni=model_config_snapshot.is_omni,
                            is_active=True,
                        )
                    ]

    return agent_cfg_snapshot, model_config_snapshot, is_shared


@router.post("", summary="创建应用（可选创建 Agent 配置）")
@cur_workspace_access_guard()
@check_app_quota
def create_app(
        payload: app_schema.AppCreate,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    app = app_service.create_app(db, user_id=current_user.id, workspace_id=workspace_id, data=payload)
    return success(data=app_schema.App.model_validate(app))


@router.get("", summary="应用列表（分页）")
@cur_workspace_access_guard()
def list_apps(
        type: str | None = None,
        visibility: str | None = None,
        status: str | None = None,
        search: str | None = None,
        tag_search: str | None = None,
        include_shared: bool = True,
        shared_only: bool = False,
        page: int = 1,
        pagesize: int = 10,
        ids: Optional[str] = None,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """列出应用

    - 默认包含本工作空间的应用和分享给本工作空间的应用
    - 设置 include_shared=false 可以只查看本工作空间的应用
    - 当提供 ids 参数时，按逗号分割获取指定应用，不分页
    - search 参数支持：应用名称模糊搜索、API Key 精确搜索
    - tag_search 参数支持：应用标签模糊搜索
    """
    from sqlalchemy import select as sa_select
    from app.models.api_key_model import ApiKey

    workspace_id = current_user.current_workspace_id
    service = app_service.AppService(db)

    # 通过 search 参数搜索：支持应用名称模糊搜索和 API Key 精确搜索
    if search:
        search = search.strip()
        # 尝试作为 API Key 精确匹配（API Key 通常较长）
        if len(search) >= 10:
            matched_id = db.execute(
                sa_select(ApiKey.resource_id).where(
                    ApiKey.workspace_id == workspace_id,
                    ApiKey.api_key == search,
                    ApiKey.resource_id.isnot(None),
                )
            ).scalar_one_or_none()
            if matched_id:
                # 找到 API Key，直接返回关联的应用
                ids = str(matched_id)

    # 当 ids 存在时，根据 ids 获取应用（不分页）
    if ids is not None:
        app_ids = [app_id.strip() for app_id in ids.split(',') if app_id.strip()]
        if app_ids:
            items_orm = app_service.get_apps_by_ids(db, app_ids, workspace_id)
            items = [service._convert_to_schema(app, workspace_id) for app in items_orm]
            # 返回标准分页格式
            meta = PageMeta(page=1, pagesize=len(items), total=len(items), hasnext=False)
            return success(data=PageData(page=meta, items=items))
        # ids 为空时，返回空列表
        meta = PageMeta(page=1, pagesize=0, total=0, hasnext=False)
        return success(data=PageData(page=meta, items=[]))

    # 正常分页查询
    items_orm, total = app_service.list_apps(
        db,
        workspace_id=workspace_id,
        type=type,
        visibility=visibility,
        status=status,
        search=search,
        tag_search=tag_search,
        include_shared=include_shared,
        shared_only=shared_only,
        page=page,
        pagesize=pagesize,
    )

    items = [service._convert_to_schema(app, workspace_id) for app in items_orm]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items))


@router.get("/my-shared-out", summary="列出本工作空间主动分享出去的记录")
@cur_workspace_access_guard()
def list_my_shared_out(
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """列出本工作空间主动分享给其他工作空间的所有记录（我的共享）"""
    workspace_id = current_user.current_workspace_id
    service = app_service.AppService(db)
    shares = service.list_my_shared_out(workspace_id=workspace_id)
    data = [app_schema.AppShare.model_validate(s) for s in shares]
    return success(data=data)


@router.delete("/share/{target_workspace_id}", summary="取消对某工作空间的所有应用分享")
@cur_workspace_access_guard()
def unshare_all_apps_to_workspace(
        target_workspace_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """Cancel all app shares from current workspace to a target workspace."""
    workspace_id = current_user.current_workspace_id
    service = app_service.AppService(db)
    count = service.unshare_all_apps_to_workspace(
        target_workspace_id=target_workspace_id,
        workspace_id=workspace_id
    )
    return success(msg=f"已取消 {count} 个应用的分享", data={"count": count})


@router.get("/{app_id}", summary="获取应用详情")
@cur_workspace_access_guard()
def get_app(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """获取应用详细信息

    - 支持获取本工作空间的应用
    - 支持获取分享给本工作空间的应用
    """
    workspace_id = current_user.current_workspace_id
    service = app_service.AppService(db)
    app = service.get_app(app_id, workspace_id)

    # 转换为 Schema 并设置 is_shared 字段
    app_schema_obj = service._convert_to_schema(app, workspace_id)
    return success(data=app_schema_obj)


@router.put("/{app_id}", summary="更新应用基本信息")
@cur_workspace_access_guard()
def update_app(
        app_id: uuid.UUID,
        payload: app_schema.AppUpdate,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    app = app_service.update_app(db, app_id=app_id, data=payload, workspace_id=workspace_id)
    return success(data=app_schema.App.model_validate(app))


@router.delete("/{app_id}", summary="删除应用")
@cur_workspace_access_guard()
def delete_app(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """删除应用

    会级联删除：
    - Agent 配置
    - 发布版本
    - 会话和消息
    """
    workspace_id = current_user.current_workspace_id
    logger.info(
        "用户请求删除应用",
        extra={
            "app_id": str(app_id),
            "user_id": str(current_user.id),
            "workspace_id": str(workspace_id)
        }
    )

    app_service.delete_app(db, app_id=app_id, workspace_id=workspace_id)

    return success(msg="应用删除成功")


@router.post("/{app_id}/copy", summary="复制应用")
@cur_workspace_access_guard()
@check_app_quota
def copy_app(
        app_id: uuid.UUID,
        new_name: Optional[str] = None,
        payload: app_schema.CopyAppRequest = None,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """复制应用（包括基础信息和配置）

    - 复制应用的基础信息（名称、描述、图标等）
    - 复制 Agent 配置（如果是 agent 类型）
    - 新应用默认为草稿状态
    - 不影响原应用
    """
    workspace_id = current_user.current_workspace_id
    # body takes precedence over query param for backward compatibility
    new_name = (payload.new_name if payload else None) or new_name
    logger.info(
        "用户请求复制应用",
        extra={
            "source_app_id": str(app_id),
            "user_id": str(current_user.id),
            "workspace_id": str(workspace_id),
            "new_name": new_name
        }
    )

    service = AppService(db)
    new_app = service.copy_app(
        app_id=app_id,
        user_id=current_user.id,
        workspace_id=workspace_id,
        new_name=new_name
    )

    return success(data=app_schema.App.model_validate(new_app), msg="应用复制成功")


@router.put("/{app_id}/config", summary="更新 Agent 配置")
@cur_workspace_access_guard()
def update_agent_config(
        app_id: uuid.UUID,
        payload: app_schema.AgentConfigUpdate,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    cfg = app_service.update_agent_config(db, app_id=app_id, data=payload, workspace_id=workspace_id)
    cfg = enrich_agent_config(cfg)
    return success(data=app_schema.AgentConfig.model_validate(cfg))


@router.get("/{app_id}/model/parameters/default", summary="获取 Agent 模型参数默认配置")
@cur_workspace_access_guard()
def get_agent_model_parameters(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    service = AppService(db)
    model_parameters = service.get_default_model_parameters(app_id=app_id)
    return success(data=model_parameters, msg="获取 Agent 模型参数默认配置")


@router.get("/{app_id}/config", summary="获取 Agent 配置")
@cur_workspace_access_guard()
def get_agent_config(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    cfg = app_service.get_agent_config(db, app_id=app_id, workspace_id=workspace_id)
    # 配置总是存在（不存在时返回默认模板）
    cfg = enrich_agent_config(cfg)
    return success(data=app_schema.AgentConfig.model_validate(cfg))


@router.get("/{app_id}/opening", summary="获取应用开场白配置")
@cur_workspace_access_guard()
def get_opening(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """返回开场白文本和预设问题，供前端对话界面初始化时展示"""
    workspace_id = current_user.current_workspace_id

    # 根据应用类型获取 features
    from app.models.app_model import App as AppModel
    app = db.get(AppModel, app_id)
    if app and app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        cfg = app_service.get_workflow_config(db=db, app_id=app_id, workspace_id=workspace_id)
        features = cfg.features or {}
    else:
        cfg = app_service.get_agent_config(db, app_id=app_id, workspace_id=workspace_id)
        features = cfg.features or {}
        if hasattr(features, "model_dump"):
            features = features.model_dump()

    opening = features.get("opening_statement", {})
    return success(data=app_schema.OpeningResponse(
        enabled=opening.get("enabled", False),
        statement=opening.get("statement"),
        suggested_questions=opening.get("suggested_questions", []),
    ))


@router.post("/{app_id}/publish", summary="发布应用（生成不可变快照）")
@cur_workspace_access_guard()
def publish_app(
        app_id: uuid.UUID,
        payload: app_schema.PublishRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    release = app_service.publish(
        db,
        app_id=app_id,
        publisher_id=current_user.id,
        workspace_id=workspace_id,
        version_name=payload.version_name,
        release_notes=payload.release_notes,
        name=payload.name,
        icon=payload.icon,
    )
    return success(data=app_schema.AppRelease.model_validate(release))


@router.get("/{app_id}/release/display_info", summary="获取发布展示信息")
@cur_workspace_access_guard()
def get_release_display_info(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """获取发布前展示信息默认值，有当前版本返回 release.name/icon，否则返回 app.name/icon"""
    workspace_id = current_user.current_workspace_id
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    release = app_service.get_current_release(db, app_id=app_id, workspace_id=workspace_id)
    if release:
        return success(data=app_schema.AppDisplayInfo(name=release.name, icon=release.icon))
    return success(data=app_schema.AppDisplayInfo(name=app.name, icon=app.icon))


@router.get("/{app_id}/release", summary="获取当前发布版本")
@cur_workspace_access_guard()
def get_current_release(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    release = app_service.get_current_release(db, app_id=app_id, workspace_id=workspace_id)
    if not release:
        return success(data=None)
    return success(data=app_schema.AppRelease.model_validate(release))


@router.get("/{app_id}/releases", summary="列出历史发布版本（倒序）")
@cur_workspace_access_guard()
def list_releases(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    releases = app_service.list_releases(db, app_id=app_id, workspace_id=workspace_id)
    data = [app_schema.AppRelease.model_validate(r) for r in releases]
    return success(data=data)


@router.post("/{app_id}/rollback/{version}", summary="回滚到指定版本")
@cur_workspace_access_guard()
def rollback(
        app_id: uuid.UUID,
        version: int,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    release = app_service.rollback(db, app_id=app_id, version=version, workspace_id=workspace_id)
    return success(data=app_schema.AppRelease.model_validate(release))


@router.post("/{app_id}/share", summary="分享应用到其他工作空间")
@cur_workspace_access_guard()
def share_app(
        app_id: uuid.UUID,
        payload: app_schema.AppShareCreate,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """分享应用到其他工作空间

    - 只能分享自己工作空间的应用
    - 不能分享到自己的工作空间
    - 同一个应用不能重复分享到同一个工作空间
    """
    workspace_id = current_user.current_workspace_id

    service = app_service.AppService(db)
    shares = service.share_app(
        app_id=app_id,
        target_workspace_ids=payload.target_workspace_ids,
        user_id=current_user.id,
        workspace_id=workspace_id,
        permission=payload.permission
    )

    data = [app_schema.AppShare.model_validate(s) for s in shares]
    return success(data=data, msg=f"应用已分享到 {len(shares)} 个工作空间")


@router.delete("/{app_id}/share/{target_workspace_id}", summary="取消应用分享")
@cur_workspace_access_guard()
def unshare_app(
        app_id: uuid.UUID,
        target_workspace_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """取消应用分享

    - 只能取消自己工作空间应用的分享
    """
    workspace_id = current_user.current_workspace_id

    service = app_service.AppService(db)
    service.unshare_app(
        app_id=app_id,
        target_workspace_id=target_workspace_id,
        workspace_id=workspace_id
    )

    return success(msg="应用分享已取消")


@router.patch("/{app_id}/share/{target_workspace_id}", summary="更新共享权限")
@cur_workspace_access_guard()
def update_share_permission(
        app_id: uuid.UUID,
        target_workspace_id: uuid.UUID,
        payload: app_schema.UpdateSharePermissionRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """更新共享权限（readonly <-> editable）

    - 只能修改自己工作空间应用的共享权限
    """
    workspace_id = current_user.current_workspace_id

    service = app_service.AppService(db)
    share = service.update_share_permission(
        app_id=app_id,
        target_workspace_id=target_workspace_id,
        permission=payload.permission,
        workspace_id=workspace_id
    )

    return success(data=app_schema.AppShare.model_validate(share))


@router.get("/{app_id}/shares", summary="列出应用的分享记录")
@cur_workspace_access_guard()
def list_app_shares(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """列出应用的所有分享记录

    - 只能查看自己工作空间应用的分享记录
    """
    workspace_id = current_user.current_workspace_id

    service = app_service.AppService(db)
    shares = service.list_app_shares(
        app_id=app_id,
        workspace_id=workspace_id
    )

    data = [app_schema.AppShare.model_validate(s) for s in shares]
    return success(data=data)


@router.delete("/shared/{source_workspace_id}", summary="批量移除某来源工作空间的所有共享应用")
@cur_workspace_access_guard()
def remove_all_shared_apps_from_workspace(
        source_workspace_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """Remove all shared apps from a specific source workspace (recipient operation)."""
    workspace_id = current_user.current_workspace_id
    service = app_service.AppService(db)
    count = service.remove_all_shared_apps_from_workspace(
        source_workspace_id=source_workspace_id,
        workspace_id=workspace_id
    )
    return success(msg=f"已移除 {count} 个共享应用", data={"count": count})


@router.delete("/{app_id}/shared", summary="移除共享给我的应用")
@cur_workspace_access_guard()
def remove_shared_app(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """被共享者从自己的工作空间移除共享应用

    - 不会删除源应用，只删除共享记录
    - 只能移除共享给自己工作空间的应用
    """
    workspace_id = current_user.current_workspace_id

    service = app_service.AppService(db)
    service.remove_shared_app(
        app_id=app_id,
        workspace_id=workspace_id
    )

    return success(msg="已移除共享应用")


@router.post("/{app_id}/draft/run", summary="试运行 Agent（使用当前草稿配置）")
@cur_workspace_access_guard()
async def draft_run(
        app_id: uuid.UUID,
        payload: app_schema.DraftRunRequest,
        current_user=Depends(get_current_user_async),
):
    """
    试运行 Agent，使用当前的草稿配置（未发布的配置）

    - 不需要发布应用即可测试
    - 使用当前的 AgentConfig 配置
    - 支持流式和非流式返回
    """
    workspace_id = current_user.current_workspace_id
    current_user_id = str(current_user.id)
    storage_type: str | None = None
    user_rag_memory_id = ""

    app = await _load_draft_run_app_snapshot(app_id, workspace_id)
    app_type = AppType(app.type)
    if app_type not in (AppType.AGENT, AppType.MULTI_AGENT, AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        raise BusinessException("只有 Agent , Workflow 类型应用支持试运行", BizCode.APP_TYPE_NOT_SUPPORTED)

    if app_type != AppType.PURE_WORKFLOW and not payload.message:
        raise BusinessException("当前应用类型要求必须传入 message", BizCode.INVALID_PARAMETER)

    if payload.user_id is None:
        payload.user_id = await _get_or_create_draft_end_user_id(
            app_id=app_id,
            workspace_id=app.workspace_id,
            other_id=current_user_id,
        )

    is_agent_new_conversation = app_type == AppType.AGENT and not payload.conversation_id
    should_prepare_conversation = (
        not is_agent_new_conversation
        and (payload.conversation_id or app_type not in (AppType.WORKFLOW, AppType.PURE_WORKFLOW))
    )

    if app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        if should_prepare_conversation:
            payload.conversation_id = await _ensure_workflow_conversation_ready(
                conversation_id=payload.conversation_id,
                app_id=app_id,
                workspace_id=workspace_id,
            )

        config, is_shared = await _load_workflow_runtime_config(
            app_id=app_id,
            app_workspace_id=app.workspace_id,
            current_release_id=app.current_release_id,
            workspace_id=workspace_id,
        )
        if payload.stream:
            logger.debug(
                "开始工作流流式试运行",
                extra={
                    "app_id": str(app_id),
                    "message_length": len(payload.message or ""),
                    "has_conversation_id": bool(payload.conversation_id)
                }
            )

            source = HitLogSource.EXTERNAL if is_shared else HitLogSource.CONSOLE

            async def event_generator():
                import json
                from app.services.workflow_service import WorkflowService as _WorkflowService
                _wf_service = _WorkflowService()
                async for event in _wf_service.run_stream(
                        app_id=app_id,
                        payload=payload,
                        config=config,
                        workspace_id=workspace_id,
                        source=source,
                        trigger_payload=payload.trigger_payload
                ):
                    event_type = event.get("event", "message")
                    event_data = event.get("data", {})
                    sse_message = f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                    yield sse_message

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )

        async with get_async_db_context() as db:
            workflow_service = WorkflowService(db)
            logger.debug(
                "开始非流式试运行",
                extra={
                    "app_id": str(app_id),
                    "message_length": len(payload.message or ""),
                    "has_conversation_id": bool(payload.conversation_id)
                }
            )

            source = HitLogSource.EXTERNAL if is_shared else HitLogSource.CONSOLE
            result = await workflow_service.run(
                app_id,
                payload,
                config,
                workspace_id,
                source=source,
                trigger_payload=payload.trigger_payload,
            )

            logger.debug(
                "工作流试运行返回结果",
                extra={
                    "result_type": str(type(result)),
                    "has_response": "response" in result if isinstance(result, dict) else False
                }
            )
            return success(
                data=result,
                msg="工作流任务执行成功"
            )
    if app_type == AppType.AGENT:
        from app.services.draft_run_service import AgentRunService

        storage_type, user_rag_memory_id = await _prepare_draft_memory_context_async(
            workspace_id=workspace_id,
            current_user=current_user,
        )

        async with get_async_db_context() as db:
            draft_service = AgentRunService(db)
            if should_prepare_conversation:
                conversation_id = await draft_service._ensure_conversation(
                    conversation_id=payload.conversation_id,
                    app_id=app_id,
                    workspace_id=workspace_id,
                    user_id=payload.user_id
                )
                payload.conversation_id = conversation_id

        agent_cfg, model_config, is_shared = await _load_agent_runtime_config(
            app_id=app_id,
            app_workspace_id=app.workspace_id,
            current_release_id=app.current_release_id,
            workspace_id=workspace_id,
        )

        if payload.stream:
            source = HitLogSource.EXTERNAL if is_shared else HitLogSource.CONSOLE

            async def event_generator():
                async with get_async_db_context() as stream_db:
                    from app.services.draft_run_service import AgentRunService as _AgentRunService
                    _draft_service = _AgentRunService(stream_db)
                    execution_mode = "sandbox" if settings.E2B_ENABLED else "in_process"
                    async for event in _draft_service.run_stream(
                            agent_config=agent_cfg,
                            model_config=model_config,
                            message=payload.message,
                            workspace_id=workspace_id,
                            conversation_id=payload.conversation_id,
                            user_id=payload.user_id or current_user_id,
                            variables=payload.variables,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            files=payload.files,
                            source=source,
                            execution_mode=execution_mode,
                    ):
                        yield event

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )

        logger.debug(
            "开始非流式试运行",
            extra={
                "app_id": str(app_id),
                "message_length": len(payload.message or ""),
                "has_conversation_id": bool(payload.conversation_id),
                "has_variables": bool(payload.variables),
                "has_files": bool(payload.files)
            }
        )

        async with get_async_db_context() as db:
            draft_service = AgentRunService(db)
            source = HitLogSource.EXTERNAL if is_shared else HitLogSource.CONSOLE
            result = await draft_service.run(
                agent_config=agent_cfg,
                model_config=model_config,
                message=payload.message,
                workspace_id=workspace_id,
                conversation_id=payload.conversation_id,
                user_id=payload.user_id or current_user_id,
                variables=payload.variables,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                files=payload.files,
                source=source
            )

        logger.debug(
            "试运行返回结果",
            extra={
                "result_type": str(type(result)),
                "result_keys": list(result.keys()) if isinstance(result, dict) else "not_dict"
            }
        )

        try:
            validated_result = app_schema.DraftRunResponse.model_validate(result)
            logger.debug("结果验证成功")
            return success(data=validated_result)
        except Exception as e:
            logger.error(
                "结果验证失败",
                extra={
                    "error": str(e),
                    "error_type": str(type(e)),
                    "result": str(result)[:200]
                }
            )
            raise

    from app.services.multi_agent_service import MultiAgentService
    from app.services.draft_run_service import AgentRunService

    storage_type, user_rag_memory_id = await _prepare_draft_memory_context_async(
        workspace_id=workspace_id,
        current_user=current_user,
    )

    async with get_async_db_context() as db:
        draft_service = AgentRunService(db)
        if should_prepare_conversation:
            conversation_id = await draft_service._ensure_conversation(
                conversation_id=payload.conversation_id,
                app_id=app_id,
                workspace_id=workspace_id,
                user_id=payload.user_id
            )
            payload.conversation_id = conversation_id

    if app_type == AppType.MULTI_AGENT:
        from app.schemas.multi_agent_schema import MultiAgentRunRequest

        multi_agent_request = MultiAgentRunRequest(
            message=payload.message,
            conversation_id=payload.conversation_id,
            user_id=payload.user_id or current_user_id,
            variables=payload.variables or {},
            use_llm_routing=True
        )

        if payload.stream:
            logger.debug(
                "开始多智能体流式试运行",
                extra={
                    "app_id": str(app_id),
                    "message_length": len(payload.message or ""),
                    "has_conversation_id": bool(payload.conversation_id)
                }
            )

            async def event_generator():
                async with get_async_db_context() as stream_db:
                    from app.services.multi_agent_service import MultiAgentService as _MultiAgentService
                    multiservice = _MultiAgentService(stream_db)
                    async for event in multiservice.run_stream(
                            app_id=app_id,
                            request=multi_agent_request,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id
                    ):
                        yield event

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )

        logger.debug(
            "开始多智能体非流式试运行",
            extra={
                "app_id": str(app_id),
                "message_length": len(payload.message or ""),
                "has_conversation_id": bool(payload.conversation_id)
            }
        )

        async with get_async_db_context() as db:
            multiservice = MultiAgentService(db)
            result = await multiservice.run(app_id, multi_agent_request)

        logger.debug(
            "多智能体试运行返回结果",
            extra={
                "result_type": str(type(result)),
                "has_response": "response" in result if isinstance(result, dict) else False
            }
        )

        return success(
            data=result,
            msg="多 Agent 任务执行成功"
        )

    return fail(
        msg="未知应用类型",
        code=422
    )


@router.post("/{app_id}/draft/run/compare", summary="多模型对比试运行")
@cur_workspace_access_guard()
async def draft_run_compare(
        app_id: uuid.UUID,
        payload: app_schema.DraftRunCompareRequest,
        current_user=Depends(get_current_user_async),
):
    """
    多模型对比试运行

    - 支持对比 1-5 个模型
    - 可以是不同的模型，也可以是同一模型的不同参数配置
    - 通过 model_parameters 覆盖默认参数
    - 支持并行或串行执行（非流式）
    - 支持流式返回（串行执行）
    - 返回每个模型的运行结果和性能对比

    使用场景：
    1. 对比不同模型的效果（GPT-4 vs Claude vs Gemini）
    2. 调优模型参数（不同 temperature 的效果对比）
    3. 性能和成本分析
    """
    workspace_id = current_user.current_workspace_id
    app = await _load_draft_run_app_snapshot(app_id, workspace_id)
    if AppType(app.type) != AppType.AGENT:
        raise BusinessException("只有 Agent 类型应用支持对比试运行", BizCode.APP_TYPE_NOT_SUPPORTED)

    storage_type, user_rag_memory_id = await _prepare_draft_memory_context_async(
        workspace_id=workspace_id,
        current_user=current_user,
    )

    logger.info(
        "多模型对比试运行",
        extra={
            "app_id": str(app_id),
            "model_count": len(payload.models),
            "parallel": payload.parallel,
            "stream": payload.stream
        }
    )

    from app.models import ModelConfig

    if payload.user_id is None:
        payload.user_id = await _get_or_create_draft_end_user_id(
            app_id=app_id,
            workspace_id=app.workspace_id,
            other_id=str(current_user.id),
        )

    agent_cfg, _, _ = await _load_agent_runtime_config(
        app_id=app_id,
        app_workspace_id=app.workspace_id,
        current_release_id=app.current_release_id,
        workspace_id=workspace_id,
    )

    async with get_async_db_context() as db:
        model_configs = []
        for model_item in payload.models:
            model_config = await db.scalar(
                select(ModelConfig)
                .where(ModelConfig.id == model_item.model_config_id)
                .options(selectinload(ModelConfig.api_keys))
            )
            if not model_config:
                from app.core.exceptions import ResourceNotFoundException
                raise ResourceNotFoundException("模型配置", str(model_item.model_config_id))

            agent_model_params = agent_cfg.model_parameters
            if hasattr(agent_model_params, 'model_dump'):
                agent_model_params = agent_model_params.model_dump()
            elif not isinstance(agent_model_params, dict):
                agent_model_params = {}

            item_model_params = model_item.model_parameters
            if hasattr(item_model_params, 'model_dump'):
                item_model_params = item_model_params.model_dump()
            elif not isinstance(item_model_params, dict):
                item_model_params = {}

            merged_parameters = {
                **(agent_model_params or {}),
                **(item_model_params or {})
            }

            model_configs.append({
                "model_config": _build_model_config_snapshot(model_config),
                "parameters": merged_parameters,
                "label": model_item.label or model_config.name,
                "model_config_id": model_item.model_config_id,
                "conversation_id": model_item.conversation_id
            })

    # 从 features 中读取功能开关（与 draft_run 保持一致）
    features_config: dict = agent_cfg.features or {}
    if hasattr(features_config, 'model_dump'):
        features_config = features_config.model_dump()
    web_search_feature = features_config.get("web_search", {})
    web_search = isinstance(web_search_feature, dict) and web_search_feature.get("enabled", False)

    # 流式返回
    if payload.stream:
        source = HitLogSource.CONSOLE
        execution_mode = "sandbox" if settings.E2B_ENABLED else "in_process"
        async def event_generator():
            from app.services.draft_run_service import AgentRunService
            async with get_async_db_context() as db:
                draft_service = AgentRunService(db)
                async for event in draft_service.run_compare_stream(
                        agent_config=agent_cfg,
                        models=model_configs,
                        message=payload.message,
                        workspace_id=workspace_id,
                        conversation_id=payload.conversation_id,
                        user_id=payload.user_id,
                        variables=payload.variables,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                        web_search=web_search,
                        memory=True,
                        parallel=payload.parallel,
                        timeout=payload.timeout or 60,
                        files=payload.files,
                        source=source,
                        execution_mode=execution_mode,
                ):
                    yield event

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    # 非流式返回
    from app.services.draft_run_service import AgentRunService
    source = HitLogSource.CONSOLE
    execution_mode = "sandbox" if settings.E2B_ENABLED else "in_process"
    async with get_async_db_context() as db:
        draft_service = AgentRunService(db)
        result = await draft_service.run_compare(
            agent_config=agent_cfg,
            models=model_configs,
            message=payload.message,
            workspace_id=workspace_id,
            conversation_id=payload.conversation_id,
            user_id=payload.user_id,
            variables=payload.variables,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            web_search=web_search,
            memory=True,
            parallel=payload.parallel,
            timeout=payload.timeout or 60,
            files=payload.files,
            source=source,
            execution_mode=execution_mode,
        )

    logger.info(
        "多模型对比完成",
        extra={
            "app_id": str(app_id),
            "successful": result["successful_count"],
            "failed": result["failed_count"]
        }
    )

    return success(data=app_schema.DraftRunCompareResponse(**result))


@router.post("/{app_id}/workflow/nodes/{node_id}/run", summary="单节点试运行")
@cur_workspace_access_guard()
async def run_single_workflow_node(
        app_id: uuid.UUID,
        node_id: str,
        payload: app_schema.NodeRunRequest,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[User, Depends(get_current_user)],
        workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)] = None,
):
    """单独执行工作流中的某个节点

    inputs 支持以下 key 格式:
    - 节点变量: "node_id.var_name"
    - 系统变量: "sys.message"、"sys.files"
    """
    workspace_id = current_user.current_workspace_id
    config = workflow_service.get_workflow_config(app_id)
    if not config:
        raise BusinessException("工作流配置不存在，无法运行", BizCode.CONFIG_MISSING)

    raw_inputs = payload.inputs or {}
    input_data = {
        "message": raw_inputs.pop("sys.message", None),
        "files": raw_inputs.pop("sys.files", None),
        "user_id": raw_inputs.pop("sys.user_id", str(current_user.id)),
        "trigger_payload": raw_inputs.pop("trigger_payload", None),
        "inputs": raw_inputs,
        "conversation_id": "",
        "conv_messages": [],
    }

    if payload.stream:
        async def event_generator():
            async for event in workflow_service.run_single_node_stream(
                    app_id=app_id,
                    node_id=node_id,
                    config=config,
                    workspace_id=workspace_id,
                    input_data=input_data,
            ):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
        )

    result = await workflow_service.run_single_node(
        app_id=app_id,
        node_id=node_id,
        config=config,
        workspace_id=workspace_id,
        input_data=input_data,
    )
    return success(data=result)


@router.get("/{app_id}/workflow/nodes/{node_id}/last_run", summary="获取节点上次运行")
@cur_workspace_access_guard()
async def get_workflow_node_last_run(
        app_id: uuid.UUID,
        node_id: str,
        source: str | None = Query(None, description="workflow_execution 或 single_node_debug"),
        db: Annotated[Session, Depends(get_db)] = None,
        current_user: Annotated[User, Depends(get_current_user)] = None,
        workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)] = None,
):
    workspace_id = current_user.current_workspace_id
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)

    if source not in (None, "workflow_execution", "single_node_debug"):
        raise BusinessException("source 参数无效", BizCode.INVALID_PARAMETER)

    result = workflow_service.get_node_last_run(app_id=app_id, node_id=node_id, source=source)
    return success(data=result)


@router.put("/{app_id}/workflow/nodes/{node_id}/cache", summary="更新节点缓存")
@cur_workspace_access_guard()
async def update_workflow_node_cache(
        app_id: uuid.UUID,
        node_id: str,
        payload: app_schema.NodeCacheUpdateRequest,
        db: Annotated[Session, Depends(get_db)] = None,
        current_user: Annotated[User, Depends(get_current_user)] = None,
        workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)] = None,
):
    workspace_id = current_user.current_workspace_id
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    result = workflow_service.update_node_cache(
        app_id=app_id,
        node_id=node_id,
        result_data=payload.result_data,
        patches=[item.model_dump() for item in payload.patches],
    )
    if not result:
        raise BusinessException("节点缓存不存在", BizCode.NOT_FOUND)
    return success(data=result)


@router.delete("/{app_id}/workflow/nodes/{node_id}/cache", summary="失效节点缓存")
@cur_workspace_access_guard()
async def invalidate_workflow_node_cache(
        app_id: uuid.UUID,
        node_id: str,
        db: Annotated[Session, Depends(get_db)] = None,
        current_user: Annotated[User, Depends(get_current_user)] = None,
        workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)] = None,
):
    workspace_id = current_user.current_workspace_id
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    affected = workflow_service.invalidate_node_cache(app_id=app_id, node_id=node_id)
    if affected <= 0:
        raise BusinessException("节点缓存不存在", BizCode.NOT_FOUND)
    return success(data={"affected": affected})


@router.post("/{app_id}/workflow/cache/reset", summary="清空工作流调试缓存")
@cur_workspace_access_guard()
async def reset_workflow_debug_cache(
        app_id: uuid.UUID,
        db: Annotated[Session, Depends(get_db)] = None,
        current_user: Annotated[User, Depends(get_current_user)] = None,
        workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)] = None,
):
    workspace_id = current_user.current_workspace_id
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    result = workflow_service.reset_workflow_debug_cache(app_id=app_id)
    return success(data=result)


@router.get("/{app_id}/workflow/debug/state", summary="获取工作流当前调试状态")
@cur_workspace_access_guard()
async def get_workflow_debug_state(
        app_id: uuid.UUID,
        db: Annotated[Session, Depends(get_db)] = None,
        current_user: Annotated[User, Depends(get_current_user)] = None,
        workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)] = None,
):
    workspace_id = current_user.current_workspace_id
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    result = workflow_service.get_workflow_debug_state(app_id=app_id)
    return success(data=result)


@router.post("/{app_id}/workflow/nodes/{node_id}/rerun", summary="基于最近一次单节点调试输入重跑")
@cur_workspace_access_guard()
async def rerun_workflow_node(
        app_id: uuid.UUID,
        node_id: str,
        payload: app_schema.NodeRerunRequest,
        db: Annotated[Session, Depends(get_db)] = None,
        current_user: Annotated[User, Depends(get_current_user)] = None,
        workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)] = None,
):
    workspace_id = current_user.current_workspace_id
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    config = workflow_service.get_workflow_config(app_id)
    if not config:
        raise BusinessException("工作流配置不存在，无法运行", BizCode.CONFIG_MISSING)
    result = await workflow_service.rerun_node_from_last_debug(
        app_id=app_id,
        node_id=node_id,
        config=config,
        workspace_id=workspace_id,
        invalidate_cache=payload.invalidate_cache,
        bypass_cache=payload.bypass_cache,
    )
    return success(data=result)


@router.get("/{app_id}/workflow")
@cur_workspace_access_guard()
async def get_workflow_config(
        app_id: Annotated[uuid.UUID, Path(description="应用 ID")],
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[User, Depends(get_current_user)]

):
    """获取工作流配置

    获取应用的工作流配置详情。
    """
    workspace_id = current_user.current_workspace_id
    cfg = app_service.get_workflow_config(db=db, app_id=app_id, workspace_id=workspace_id)
    # 配置总是存在（不存在时返回默认模板）
    return success(data=WorkflowConfigSchema.model_validate(cfg))


@router.put("/{app_id}/workflow", summary="更新 Workflow 配置")
@cur_workspace_access_guard()
async def update_workflow_config(
        app_id: uuid.UUID,
        payload: WorkflowConfigUpdate,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[User, Depends(get_current_user)]
):
    workspace_id = current_user.current_workspace_id
    if payload.variables:
        from app.services.workflow_service import WorkflowService
        resolved = await WorkflowService(db)._resolve_variables_file_defaults(
            [v.model_dump() for v in payload.variables]
        )
        # Patch default values back into VariableDefinition objects
        for var_def, resolved_def in zip(payload.variables, resolved):
            var_def.default = resolved_def.get("default", var_def.default)
    cfg = app_service.update_workflow_config(db, app_id=app_id, data=payload, workspace_id=workspace_id)
    # cache_key = workflow_config_key(app_id)
    # await delete_json_async(cache_key)
    # logger.info(f"[cache] invalidate workflow config: key={cache_key}")
    return success(data=WorkflowConfigSchema.model_validate(cfg))


@router.get("/{app_id}/workflow/export")
@cur_workspace_access_guard()
async def export_workflow_config(
        app_id: uuid.UUID,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[User, Depends(get_current_user)]
):
    """导出工作流配置为YAML文件"""
    workflow_service = WorkflowService(db)

    return success(data={
        "content": workflow_service.export_workflow_dsl(app_id=app_id),
    })


@router.post("/workflow/import")
@cur_workspace_access_guard()
async def import_workflow_config(
        file: UploadFile = File(...),
        platform: str = Form(...),
        app_id: str = Form(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)

):
    """从YAML内容导入工作流配置"""
    if not file.filename.lower().endswith((".yaml", ".yml")):
        return fail(msg="Only yaml file is allowed", code=BizCode.BAD_REQUEST)

    raw_text = (await file.read()).decode("utf-8")
    import_service = WorkflowImportService(db)
    config = yaml.safe_load(raw_text)
    result = await import_service.upload_config(platform, config)
    return success(data=result)


@router.post("/workflow/import/save")
@cur_workspace_access_guard()
@check_app_quota
async def save_workflow_import(
        data: WorkflowImportSave,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    import_service = WorkflowImportService(db)
    app = await import_service.save_workflow(
        user_id=current_user.id,
        workspace_id=current_user.current_workspace_id,
        temp_id=data.temp_id,
        name=data.name,
        description=data.description,
    )
    return success(data=app_schema.App.model_validate(app))


@router.get("/{app_id}/statistics", summary="应用统计数据")
@cur_workspace_access_guard()
def get_app_statistics(
        app_id: uuid.UUID,
        start_date: int,
        end_date: int,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """获取应用统计数据

    Args:
        app_id: 应用ID
        start_date: 开始时间戳（毫秒）
        end_date: 结束时间戳（毫秒）
        db: 数据库连接
        current_user: 当前用户

    Returns:
        - daily_conversations: 每日会话数统计
        - total_conversations: 总会话数
        - daily_new_users: 每日新增用户数
        - total_new_users: 总新增用户数
        - daily_api_calls: 每日API调用次数
        - total_api_calls: 总API调用次数
        - daily_tokens: 每日token消耗
        - total_tokens: 总token消耗
    """
    workspace_id = current_user.current_workspace_id
    stats_service = AppStatisticsService(db)

    result = stats_service.get_app_statistics(
        app_id=app_id,
        workspace_id=workspace_id,
        start_date=start_date,
        end_date=end_date
    )

    return success(data=result)


@router.get("/workspace/api-statistics", summary="工作空间API调用统计")
@cur_workspace_access_guard()
def get_workspace_api_statistics(
        start_date: int,
        end_date: int,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """获取工作空间API调用统计
    
    Args:
        start_date: 开始时间戳（毫秒）
        end_date: 结束时间戳（毫秒）
        db: 数据库连接
        current_user: 当前用户
    
    Returns:
        每日统计数据列表，每项包含：
        - date: 日期
        - total_calls: 当日总调用次数
        - app_calls: 当日应用调用次数
        - service_calls: 当日服务调用次数
    """
    workspace_id = current_user.current_workspace_id
    stats_service = AppStatisticsService(db)

    result = stats_service.get_workspace_api_statistics(
        workspace_id=workspace_id,
        start_date=start_date,
        end_date=end_date
    )

    return success(data=result)


@router.get("/{app_id}/export", summary="导出应用配置为 YAML 文件")
@cur_workspace_access_guard()
async def export_app(
        app_id: uuid.UUID,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[User, Depends(get_current_user)],
        release_id: Optional[uuid.UUID] = None
):
    """导出 agent / multi_agent / workflow 应用配置为 YAML 文件流。
    release_id: 指定发布版本id，不传则导出当前草稿配置。
    """
    yaml_str, filename = AppDslService(db).export_dsl(app_id, release_id)
    encoded = quote(filename, safe=".")
    yaml_bytes = yaml_str.encode("utf-8")
    file_stream = io.BytesIO(yaml_bytes)
    file_stream.seek(0)
    return StreamingResponse(
        file_stream,
        media_type="application/octet-stream; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
                 "Content-Length": str(len(yaml_bytes))}
    )


@router.post("/import", summary="从 YAML 文件导入应用")
@cur_workspace_access_guard()
async def import_app(
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        app_id: Optional[str] = Form(None),
):
    """从 YAML 文件导入 agent / multi_agent / workflow 应用。
    传入 app_id 时覆盖该应用的配置（类型必须一致），否则创建新应用。
    跨空间/跨租户导入时，模型/工具/知识库会按名称匹配，匹配不到则置空并返回 warnings。
    """
    if not file.filename.lower().endswith((".yaml", ".yml")):
        return fail(msg="仅支持 YAML 文件", code=BizCode.BAD_REQUEST)

    raw = (await file.read()).decode("utf-8")
    dsl = yaml.safe_load(raw)
    if not dsl or "app" not in dsl:
        return fail(msg="YAML 格式无效，缺少 app 字段", code=BizCode.BAD_REQUEST)

    target_app_id = uuid.UUID(app_id) if app_id else None
    # 仅新建应用时检查配额，覆盖已有应用时跳过
    if target_app_id is None:
        from app.core.quota_manager import _check_quota
        _check_quota(db, current_user.tenant_id, "app_quota", "app", workspace_id=current_user.current_workspace_id)
    result_app, warnings = AppDslService(db).import_dsl(
        dsl=dsl,
        workspace_id=current_user.current_workspace_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        app_id=target_app_id,
    )
    return success(
        data={"app": app_schema.App.model_validate(result_app), "warnings": warnings},
        msg="应用导入成功" + ("，但部分资源需手动配置" if warnings else "")
    )


@router.post("/{app_id}/publish_tool", summary="发布工作流为工具")
@cur_workspace_access_guard()
async def publish_workflow_as_tool(
        app_id: uuid.UUID,
        payload: tool_schema.WorkflowToolCreateRequest,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    from app.services.workflow_tool_publish_service import WorkflowToolPublishService
    from app.services.tool_service import ToolService

    service = WorkflowToolPublishService(db)
    tool_config = service.publish_workflow_as_tool(
        workflow_app_id=app_id,
        tool_name=payload.name,
        tool_description=payload.description or "",
        tenant_id=current_user.tenant_id,
        workspace_id=current_user.current_workspace_id,
        icon=payload.icon,
        timeout=payload.timeout,
        tags=payload.tags,
        release_id=uuid.UUID(payload.release_id) if payload.release_id else None,
    )

    detail = ToolService(db).get_tool_detail(str(tool_config.id), current_user.tenant_id)
    return success(data=detail or tool_schema.ToolDetailSchema.model_validate(tool_config))


@router.get("/{app_id}/publish_tool/preview", summary="预览工作流发布为工具的入参和出参")
@cur_workspace_access_guard()
async def preview_workflow_tool_publish(
        app_id: uuid.UUID,
        release_id: uuid.UUID | None = Query(None, description="指定工作流发布版本ID，不传则使用当前发布版本"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    from app.services.workflow_tool_publish_service import WorkflowToolPublishService

    preview = WorkflowToolPublishService(db).get_publish_preview(
        workflow_app_id=app_id,
        workspace_id=current_user.current_workspace_id,
        release_id=release_id,
    )
    return success(data=preview)


@router.get("/citations/{document_id}/download", summary="下载引用文档原始文件")
async def download_citation_file(
        document_id: uuid.UUID = Path(..., description="引用文档ID"),
        db: Session = Depends(get_db),
        storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """
    下载引用文档的原始文件。
    仅当应用功能特性 citation.allow_download=true 时，前端才会展示此下载链接。
    路由本身不做权限校验，由业务层通过 allow_download 开关控制入口。
    """
    from fastapi import HTTPException, status as http_status
    from app.models.document_model import Document
    from app.models.file_model import File as FileModel

    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="文档不存在")

    file_record = db.query(FileModel).filter(FileModel.id == doc.file_id).first()
    if not file_record:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="原始文件不存在")

    try:
        content = await storage_service.download_file(file_record.file_key)
    except Exception as e:
        logger.error(f"Storage download failed: {e}")
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="文件未找到")

    encoded_name = quote(doc.file_name)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"}
    )
