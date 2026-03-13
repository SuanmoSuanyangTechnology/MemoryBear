import uuid
import io
from typing import Optional, Annotated

import yaml
from fastapi import APIRouter, Depends, Path, Form, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from urllib.parse import quote

from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.core.response_utils import success, fail
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.models import User
from app.models.app_model import AppType
from app.repositories import knowledge_repository
from app.repositories.end_user_repository import EndUserRepository
from app.schemas import app_schema
from app.schemas.response_schema import PageData, PageMeta
from app.schemas.workflow_schema import WorkflowConfig as WorkflowConfigSchema
from app.schemas.workflow_schema import WorkflowConfigUpdate, WorkflowImportSave
from app.services import app_service, workspace_service
from app.services.agent_config_helper import enrich_agent_config
from app.services.app_service import AppService
from app.services.app_statistics_service import AppStatisticsService
from app.services.workflow_import_service import WorkflowImportService
from app.services.workflow_service import WorkflowService, get_workflow_service
from app.services.app_dsl_service import AppDslService

router = APIRouter(prefix="/apps", tags=["Apps"])
logger = get_business_logger()


@router.post("", summary="创建应用（可选创建 Agent 配置）")
@cur_workspace_access_guard()
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
        include_shared: bool = True,
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
    """
    workspace_id = current_user.current_workspace_id
    service = app_service.AppService(db)

    # 当 ids 存在且不为 None 时，根据 ids 获取应用
    if ids is not None:
        app_ids = [app_id.strip() for app_id in ids.split(',') if app_id.strip()]
        items_orm = app_service.get_apps_by_ids(db, app_ids, workspace_id)
        items = [service._convert_to_schema(app, workspace_id) for app in items_orm]
        return success(data=items)

    # 正常分页查询
    items_orm, total = app_service.list_apps(
        db,
        workspace_id=workspace_id,
        type=type,
        visibility=visibility,
        status=status,
        search=search,
        include_shared=include_shared,
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
def copy_app(
        app_id: uuid.UUID,
        new_name: Optional[str] = None,
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
        release_notes=payload.release_notes
    )
    return success(data=app_schema.AppRelease.model_validate(release))


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
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
        workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)] = None
):
    """
    试运行 Agent，使用当前的草稿配置（未发布的配置）

    - 不需要发布应用即可测试
    - 使用当前的 AgentConfig 配置
    - 支持流式和非流式返回
    """
    workspace_id = current_user.current_workspace_id

    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None:
        storage_type = 'neo4j'
    user_rag_memory_id = ''
    if workspace_id:

        knowledge = knowledge_repository.get_knowledge_by_name(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id
        )
        if knowledge:
            user_rag_memory_id = str(knowledge.id)

    # 提前验证和准备（在流式响应开始前完成）
    from app.services.app_service import AppService
    from app.services.multi_agent_service import MultiAgentService
    from app.models import AgentConfig, ModelConfig
    from sqlalchemy import select
    from app.core.exceptions import BusinessException
    from app.services.draft_run_service import AgentRunService

    service = AppService(db)
    draft_service = AgentRunService(db)

    # 1. 验证应用
    app = service._get_app_or_404(app_id)
    if app.type != AppType.AGENT and app.type != AppType.MULTI_AGENT and app.type != AppType.WORKFLOW:
        raise BusinessException("只有 Agent , Workflow 类型应用支持试运行", BizCode.APP_TYPE_NOT_SUPPORTED)

    # 只读操作，允许访问共享应用
    service._validate_app_accessible(app, workspace_id)

    if payload.user_id is None:
        end_user_repo = EndUserRepository(db)
        new_end_user = end_user_repo.get_or_create_end_user(
            app_id=app_id,
            other_id=str(current_user.id),
            original_user_id=str(current_user.id)  # Save original user_id to other_id
        )
        payload.user_id = str(new_end_user.id)

    # 处理会话ID（创建或验证）
    conversation_id = await draft_service._ensure_conversation(
        conversation_id=payload.conversation_id,
        app_id=app_id,
        workspace_id=workspace_id,
        user_id=payload.user_id
    )
    payload.conversation_id = conversation_id

    if app.type == AppType.AGENT:
        service._check_agent_config(app_id)

        # 2. 获取 Agent 配置
        stmt = select(AgentConfig).where(AgentConfig.app_id == app_id)
        agent_cfg = db.scalars(stmt).first()
        if not agent_cfg:
            raise BusinessException("Agent 配置不存在", BizCode.AGENT_CONFIG_MISSING)

        # 3. 获取模型配置
        model_config = None
        if agent_cfg.default_model_config_id:
            model_config = db.get(ModelConfig, agent_cfg.default_model_config_id)
            if not model_config:
                from app.core.exceptions import ResourceNotFoundException
                raise ResourceNotFoundException("模型配置", str(agent_cfg.default_model_config_id))

        # 流式返回
        if payload.stream:
            async def event_generator():

                async for event in draft_service.run_stream(
                        agent_config=agent_cfg,
                        model_config=model_config,
                        message=payload.message,
                        workspace_id=workspace_id,
                        conversation_id=payload.conversation_id,
                        user_id=payload.user_id or str(current_user.id),
                        variables=payload.variables,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                        files=payload.files  # 传递多模态文件
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
        logger.debug(
            "开始非流式试运行",
            extra={
                "app_id": str(app_id),
                "message_length": len(payload.message),
                "has_conversation_id": bool(payload.conversation_id),
                "has_variables": bool(payload.variables),
                "has_files": bool(payload.files)
            }
        )

        from app.services.draft_run_service import AgentRunService
        draft_service = AgentRunService(db)
        result = await draft_service.run(
            agent_config=agent_cfg,
            model_config=model_config,
            message=payload.message,
            workspace_id=workspace_id,
            conversation_id=payload.conversation_id,
            user_id=payload.user_id or str(current_user.id),
            variables=payload.variables,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            files=payload.files  # 传递多模态文件
        )

        logger.debug(
            "试运行返回结果",
            extra={
                "result_type": str(type(result)),
                "result_keys": list(result.keys()) if isinstance(result, dict) else "not_dict"
            }
        )

        # 验证结果
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
    elif app.type == AppType.MULTI_AGENT:
        # 1. 检查多智能体配置完整性
        service._check_multi_agent_config(app_id)

        # 2. 构建多智能体运行请求
        from app.schemas.multi_agent_schema import MultiAgentRunRequest

        multi_agent_request = MultiAgentRunRequest(
            message=payload.message,
            conversation_id=payload.conversation_id,
            user_id=payload.user_id or str(current_user.id),
            variables=payload.variables or {},
            use_llm_routing=True  # 默认启用 LLM 路由
        )

        # 3. 流式返回
        if payload.stream:
            logger.debug(
                "开始多智能体流式试运行",
                extra={
                    "app_id": str(app_id),
                    "message_length": len(payload.message),
                    "has_conversation_id": bool(payload.conversation_id)
                }
            )

            async def event_generator():
                """多智能体流式事件生成器"""
                multiservice = MultiAgentService(db)

                # 调用多智能体服务的流式方法
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

        # 4. 非流式返回
        logger.debug(
            "开始多智能体非流式试运行",
            extra={
                "app_id": str(app_id),
                "message_length": len(payload.message),
                "has_conversation_id": bool(payload.conversation_id)
            }
        )

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
    elif app.type == AppType.WORKFLOW:  # 工作流
        config = workflow_service.check_config(app_id)
        # 3. 流式返回
        if payload.stream:
            logger.debug(
                "开始工作流流式试运行",
                extra={
                    "app_id": str(app_id),
                    "message_length": len(payload.message),
                    "has_conversation_id": bool(payload.conversation_id)
                }
            )

            async def event_generator():
                """工作流事件生成器
                
                将事件转换为标准 SSE 格式：
                event: <event_type>
                data: <json_data>
                """
                import json

                # 调用工作流服务的流式方法
                async for event in workflow_service.run_stream(
                        app_id=app_id,
                        payload=payload,
                        config=config,
                        workspace_id=current_user.current_workspace_id
                ):
                    # 提取事件类型和数据
                    event_type = event.get("event", "message")
                    event_data = event.get("data", {})

                    # 转换为标准 SSE 格式（字符串）
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

        # 4. 非流式返回
        logger.debug(
            "开始非流式试运行",
            extra={
                "app_id": str(app_id),
                "message_length": len(payload.message),
                "has_conversation_id": bool(payload.conversation_id)
            }
        )

        result = await workflow_service.run(app_id, payload, config, current_user.current_workspace_id)

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
    else:
        return fail(
            msg="未知应用类型",
            code=422
        )


@router.post("/{app_id}/draft/run/compare", summary="多模型对比试运行")
@cur_workspace_access_guard()
async def draft_run_compare(
        app_id: uuid.UUID,
        payload: app_schema.DraftRunCompareRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
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

    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None:
        storage_type = 'neo4j'
    user_rag_memory_id = ''
    if workspace_id:
        knowledge = knowledge_repository.get_knowledge_by_name(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id
        )
        if knowledge:
            user_rag_memory_id = str(knowledge.id)

    logger.info(
        "多模型对比试运行",
        extra={
            "app_id": str(app_id),
            "model_count": len(payload.models),
            "parallel": payload.parallel,
            "stream": payload.stream
        }
    )

    # 提前验证和准备（在流式响应开始前完成）
    from app.services.app_service import AppService
    from app.models import ModelConfig

    service = AppService(db)

    # 1. 验证应用和权限
    app = service._get_app_or_404(app_id)
    if app.type != "agent":
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode
        raise BusinessException("只有 Agent 类型应用支持试运行", BizCode.APP_TYPE_NOT_SUPPORTED)
    service._validate_app_accessible(app, workspace_id)

    if payload.user_id is None:
        end_user_repo = EndUserRepository(db)
        new_end_user = end_user_repo.get_or_create_end_user(
            app_id=app_id,
            other_id=str(current_user.id),
            original_user_id=str(current_user.id)  # Save original user_id to other_id
        )
        payload.user_id = str(new_end_user.id)

    # 2. 获取 Agent 配置
    from sqlalchemy import select
    from app.models import AgentConfig
    stmt = select(AgentConfig).where(AgentConfig.app_id == app_id)
    agent_cfg = db.scalars(stmt).first()
    if not agent_cfg:
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode
        raise BusinessException("Agent 配置不存在", BizCode.AGENT_CONFIG_MISSING)

    # 3. 验证所有模型配置
    model_configs = []
    for model_item in payload.models:
        model_config = db.get(ModelConfig, model_item.model_config_id)
        if not model_config:
            from app.core.exceptions import ResourceNotFoundException
            raise ResourceNotFoundException("模型配置", str(model_item.model_config_id))

        # 获取 agent_cfg.model_parameters，如果是 ModelParameters 对象则转为字典
        agent_model_params = agent_cfg.model_parameters
        if hasattr(agent_model_params, 'model_dump'):
            agent_model_params = agent_model_params.model_dump()
        elif not isinstance(agent_model_params, dict):
            agent_model_params = {}

        # 获取 model_item.model_parameters，如果是 ModelParameters 对象则转为字典
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
            "model_config": model_config,
            "parameters": merged_parameters,
            "label": model_item.label or model_config.name,
            "model_config_id": model_item.model_config_id,
            "conversation_id": model_item.conversation_id  # 传递每个模型的 conversation_id
        })



    # 流式返回
    if payload.stream:
        async def event_generator():
            from app.services.draft_run_service import AgentRunService
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
                    web_search=True,
                    memory=True,
                    parallel=payload.parallel,
                    timeout=payload.timeout or 60,
                    files=payload.files
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
        web_search=True,
        memory=True,
        parallel=payload.parallel,
        timeout=payload.timeout or 60,
        files=payload.files
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
    cfg = app_service.update_workflow_config(db, app_id=app_id, data=payload, workspace_id=workspace_id)
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
        headers={"Content-Disposition": f"attachment; filename={encoded}",
                 "Content-Length": str(len(yaml_bytes))}
    )


@router.post("/import", summary="从 YAML 文件导入应用")
@cur_workspace_access_guard()
async def import_app(
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """从 YAML 文件导入 agent / multi_agent / workflow 应用。
    跨空间/跨租户导入时，模型/工具/知识库会按名称匹配，匹配不到则置空并返回 warnings。
    """
    if not file.filename.lower().endswith((".yaml", ".yml")):
        return fail(msg="仅支持 YAML 文件", code=BizCode.BAD_REQUEST)

    raw = (await file.read()).decode("utf-8")
    dsl = yaml.safe_load(raw)
    if not dsl or "app" not in dsl:
        return fail(msg="YAML 格式无效，缺少 app 字段", code=BizCode.BAD_REQUEST)

    new_app, warnings = AppDslService(db).import_dsl(
        dsl=dsl,
        workspace_id=current_user.current_workspace_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )
    return success(
        data={"app": app_schema.App.model_validate(new_app), "warnings": warnings},
        msg="应用导入成功" + ("，但部分资源需手动配置" if warnings else "")
    )
