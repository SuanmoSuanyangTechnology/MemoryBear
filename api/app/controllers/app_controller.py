import uuid
from typing import Optional, Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.models import User
from app.models.app_model import AppType, App
from app.repositories import knowledge_repository
from app.schemas import app_schema
from app.schemas.response_schema import PageData, PageMeta
from app.schemas.workflow_schema import WorkflowConfigUpdate
from app.services import app_service, workspace_service
from app.services.agent_config_helper import enrich_agent_config
from app.services.app_service import AppService
from app.schemas.workflow_schema import WorkflowConfig as WorkflowConfigSchema
from app.services.workflow_service import WorkflowService, get_workflow_service

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
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """列出应用

    - 默认包含本工作空间的应用和分享给本工作空间的应用
    - 设置 include_shared=false 可以只查看本工作空间的应用
    """
    workspace_id = current_user.current_workspace_id
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

    # 使用 AppService 的转换方法来设置 is_shared 字段
    service = app_service.AppService(db)
    items = [service._convert_to_schema(app, workspace_id) for app in items_orm]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items))

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
        version_name = payload.version_name,
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
        workspace_id=workspace_id
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
    from app.services.draft_run_service import DraftRunService

    service = AppService(db)
    draft_service = DraftRunService(db)

    # 1. 验证应用
    app = service._get_app_or_404(app_id)
    if app.type != AppType.AGENT and app.type != AppType.MULTI_AGENT and app.type != AppType.WORKFLOW:
        raise BusinessException("只有 Agent , Workflow 类型应用支持试运行", BizCode.APP_TYPE_NOT_SUPPORTED)

    # 只读操作，允许访问共享应用
    service._validate_app_accessible(app, workspace_id)

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

        # 非流式返回
        logger.debug(
            "开始非流式试运行",
            extra={
                "app_id": str(app_id),
                "message_length": len(payload.message),
                "has_conversation_id": bool(payload.conversation_id),
                "has_variables": bool(payload.variables)
            }
        )

        from app.services.draft_run_service import DraftRunService
        draft_service = DraftRunService(db)
        result = await draft_service.run(
            agent_config=agent_cfg,
            model_config=model_config,
            message=payload.message,
            workspace_id=workspace_id,
            conversation_id=payload.conversation_id,
            user_id=payload.user_id or str(current_user.id),
            variables=payload.variables,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id
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
            user_id=payload.user_id,
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
    elif app.type == AppType.WORKFLOW: #工作流
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
                        config=config
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

        result = await workflow_service.run(app_id, payload,config)

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
    if storage_type is None: storage_type = 'neo4j'
    user_rag_memory_id = ''
    if workspace_id:
        knowledge = knowledge_repository.get_knowledge_by_name(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id
        )
        if knowledge: user_rag_memory_id = str(knowledge.id)

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

        merged_parameters = {
            **(agent_cfg.model_parameters or {}),
            **(model_item.model_parameters or {})
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
            from app.services.draft_run_service import DraftRunService
            draft_service = DraftRunService(db)
            async for event in draft_service.run_compare_stream(
                agent_config=agent_cfg,
                models=model_configs,
                message=payload.message,
                workspace_id=workspace_id,
                conversation_id=payload.conversation_id,
                user_id=payload.user_id or str(current_user.id),
                variables=payload.variables,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                web_search=True,
                memory=True,
                parallel=payload.parallel,
                timeout=payload.timeout or 60
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
    from app.services.draft_run_service import DraftRunService
    draft_service = DraftRunService(db)
    result = await draft_service.run_compare(
        agent_config=agent_cfg,
        models=model_configs,
        message=payload.message,
        workspace_id=workspace_id,
        conversation_id=payload.conversation_id,
        user_id=payload.user_id or str(current_user.id),
        variables=payload.variables,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        web_search=True,
        memory=True,
        parallel=payload.parallel,
        timeout=payload.timeout or 60
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

