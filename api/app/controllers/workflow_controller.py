"""
工作流 API 控制器
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard

from app.models.user_model import User
from app.models.app_model import App
from app.services.workflow_service import WorkflowService, get_workflow_service
from app.schemas.workflow_schema import (
    WorkflowConfigCreate,
    WorkflowConfigUpdate,
    WorkflowConfig,
    WorkflowValidationResponse,
    WorkflowExecution,
    WorkflowNodeExecution,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse
)
from app.core.response_utils import success, fail
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["workflow"])


# ==================== 工作流配置管理 ====================

@router.post("/{app_id}/workflow")
@cur_workspace_access_guard()
async def create_workflow_config(
    app_id: Annotated[uuid.UUID, Path(description="应用 ID")],
    config: WorkflowConfigCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkflowService, Depends(get_workflow_service)]
):
    """创建工作流配置

    创建或更新应用的工作流配置。配置会进行基础验证，但允许保存不完整的配置（草稿）。
    """
    try:
        # 验证应用是否存在且属于当前工作空间
        app = db.query(App).filter(
            App.id == app_id,
            App.workspace_id == current_user.current_workspace_id,
            App.is_active == True
        ).first()

        if not app:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="应用不存在或无权访问"
            )

        # 验证应用类型
        if app.type != "workflow":
            return fail(
                code=BizCode.INVALID_PARAMETER,
                msg=f"应用类型必须为 workflow，当前为 {app.type}"
            )

        # 创建工作流配置
        workflow_config = service.create_workflow_config(
            app_id=app_id,
            nodes=[node.model_dump() for node in config.nodes],
            edges=[edge.model_dump() for edge in config.edges],
            variables=[var.model_dump() for var in config.variables],
            execution_config=config.execution_config.model_dump(),
            triggers=[trigger.model_dump() for trigger in config.triggers],
            validate=True  # 进行基础验证
        )

        return success(
            data=WorkflowConfig.model_validate(workflow_config),
            msg="工作流配置创建成功"
        )

    except BusinessException as e:
        logger.warning(f"创建工作流配置失败: {e.message}")
        return fail(code=e.error_code, msg=e.message)
    except Exception as e:
        logger.error(f"创建工作流配置异常: {e}", exc_info=True)
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg=f"创建工作流配置失败: {str(e)}"
        )

#
# @router.get("/{app_id}/workflow")
# async def get_workflow_config(
#     app_id: Annotated[uuid.UUID, Path(description="应用 ID")],
#     db: Annotated[Session, Depends(get_db)],
#     current_user: Annotated[User, Depends(get_current_user)]
#
# ):
#     """获取工作流配置
#
#     获取应用的工作流配置详情。
#     """
#     try:
#         # 验证应用是否存在且属于当前工作空间
#         app = db.query(App).filter(
#             App.id == app_id,
#             App.workspace_id == current_user.current_workspace_id,
#             App.is_active == True
#         ).first()
#
#         if not app:
#             return fail(
#                 code=BizCode.NOT_FOUND,
#                 msg="应用不存在或无权访问"
#             )
#
#         # 获取工作流配置
#         service = WorkflowService(db)
#         workflow_config = service.get_workflow_config(app_id)
#
#         if not workflow_config:
#             return fail(
#                 code=BizCode.NOT_FOUND,
#                 msg="工作流配置不存在"
#             )
#
#         return success(
#             data=WorkflowConfig.model_validate(workflow_config)
#         )
#
#     except Exception as e:
#         logger.error(f"获取工作流配置异常: {e}", exc_info=True)
#         return fail(
#             code=BizCode.INTERNAL_ERROR,
#             msg=f"获取工作流配置失败: {str(e)}"
#         )


# @router.put("/{app_id}/workflow")
# async def update_workflow_config(
#     app_id: Annotated[uuid.UUID, Path(description="应用 ID")],
#     config: WorkflowConfigUpdate,
#     db: Annotated[Session, Depends(get_db)],
#     current_user: Annotated[User, Depends(get_current_user)],
#     service: Annotated[WorkflowService, Depends(get_workflow_service)]
# ):
#     """更新工作流配置

#     更新应用的工作流配置。可以部分更新，未提供的字段保持不变。
#     """
#     try:
#         # 验证应用是否存在且属于当前工作空间
#         app = db.query(App).filter(
#             App.id == app_id,
#             App.workspace_id == current_user.current_workspace_id,
#             App.is_active == True
#         ).first()

#         if not app:
#             return fail(
#                 code=BizCode.NOT_FOUND,
#                 msg="应用不存在或无权访问"
#             )

#         # 更新工作流配置
#         workflow_config = service.update_workflow_config(
#             app_id=app_id,
#             nodes=[node.model_dump() for node in config.nodes] if config.nodes else None,
#             edges=[edge.model_dump() for edge in config.edges] if config.edges else None,
#             variables=[var.model_dump() for var in config.variables] if config.variables else None,
#             execution_config=config.execution_config.model_dump() if config.execution_config else None,
#             triggers=[trigger.model_dump() for trigger in config.triggers] if config.triggers else None,
#             validate=True
#         )

#         return success(
#             data=WorkflowConfig.model_validate(workflow_config),
#             msg="工作流配置更新成功"
#         )

#     except BusinessException as e:
#         logger.warning(f"更新工作流配置失败: {e.message}")
#         return fail(code=e.error_code, msg=e.message)
#     except Exception as e:
#         logger.error(f"更新工作流配置异常: {e}", exc_info=True)
#         return fail(
#             code=BizCode.INTERNAL_ERROR,
#             msg=f"更新工作流配置失败: {str(e)}"
#         )


@router.delete("/{app_id}/workflow")
async def delete_workflow_config(
    app_id: Annotated[uuid.UUID, Path(description="应用 ID")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkflowService, Depends(get_workflow_service)]
):
    """删除工作流配置

    删除应用的工作流配置。
    """
    try:
        # 验证应用是否存在且属于当前工作空间
        app = db.query(App).filter(
            App.id == app_id,
            App.workspace_id == current_user.current_workspace_id,
            App.is_active == True
        ).first()

        if not app:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="应用不存在或无权访问"
            )

        # 删除工作流配置
        deleted = service.delete_workflow_config(app_id)

        if not deleted:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="工作流配置不存在"
            )

        return success(msg="工作流配置删除成功")

    except Exception as e:
        logger.error(f"删除工作流配置异常: {e}", exc_info=True)
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg=f"删除工作流配置失败: {str(e)}"
        )


@router.post("/{app_id}/workflow/validate")
async def validate_workflow_config(
    app_id: Annotated[uuid.UUID, Path(description="应用 ID")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkflowService, Depends(get_workflow_service)],
    for_publish: Annotated[bool, Query(description="是否为发布验证")] = False
):
    """验证工作流配置

    验证工作流配置是否有效。可以选择是否进行发布级别的严格验证。
    """
    try:
        # 验证应用是否存在且属于当前工作空间
        app = db.query(App).filter(
            App.id == app_id,
            App.workspace_id == current_user.current_workspace_id,
            App.is_active == True
        ).first()

        if not app:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="应用不存在或无权访问"
            )

        # 验证工作流配置

        if for_publish:
            is_valid, errors = service.validate_workflow_config_for_publish(app_id)
        else:
            workflow_config = service.get_workflow_config(app_id)
            if not workflow_config:
                return fail(
                    code=BizCode.NOT_FOUND,
                    msg="工作流配置不存在"
                )

            from app.core.workflow.validator import validate_workflow_config as validate_config
            config_dict = {
                "nodes": workflow_config.nodes,
                "edges": workflow_config.edges,
                "variables": workflow_config.variables,
                "execution_config": workflow_config.execution_config,
                "triggers": workflow_config.triggers
            }
            is_valid, errors = validate_config(config_dict, for_publish=False)

        return success(
            data=WorkflowValidationResponse(
                is_valid=is_valid,
                errors=errors,
                warnings=[]
            )
        )

    except BusinessException as e:
        logger.warning(f"验证工作流配置失败: {e.message}")
        return fail(code=e.error_code, msg=e.message)
    except Exception as e:
        logger.error(f"验证工作流配置异常: {e}", exc_info=True)
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg=f"验证工作流配置失败: {str(e)}"
        )


# ==================== 工作流执行管理 ====================

@router.get("/{app_id}/workflow/executions")
async def get_workflow_executions(
    app_id: Annotated[uuid.UUID, Path(description="应用 ID")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkflowService, Depends(get_workflow_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0
):
    """获取工作流执行记录列表

    获取应用的工作流执行历史记录。
    """
    try:
        # 验证应用是否存在且属于当前工作空间
        app = db.query(App).filter(
            App.id == app_id,
            App.workspace_id == current_user.current_workspace_id,
            App.is_active == True
        ).first()

        if not app:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="应用不存在或无权访问"
            )

        # 获取执行记录
        executions = service.get_executions_by_app(app_id, limit, offset)

        # 获取统计信息
        statistics = service.get_execution_statistics(app_id)

        return success(
            data={
                "executions": [WorkflowExecution.model_validate(e) for e in executions],
                "statistics": statistics,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": statistics["total"]
                }
            }
        )

    except Exception as e:
        logger.error(f"获取工作流执行记录异常: {e}", exc_info=True)
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg=f"获取工作流执行记录失败: {str(e)}"
        )


@router.get("/workflow/executions/{execution_id}")
async def get_workflow_execution(
    execution_id: Annotated[str, Path(description="执行 ID")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkflowService, Depends(get_workflow_service)]
):
    """获取工作流执行详情

    获取单个工作流执行的详细信息，包括所有节点的执行记录。
    """
    try:
        # 获取执行记录
        execution = service.get_execution(execution_id)

        if not execution:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="执行记录不存在"
            )

        # 验证应用是否属于当前工作空间
        app = db.query(App).filter(
            App.id == execution.app_id,
            App.workspace_id == current_user.current_workspace_id,
            App.is_active == True
        ).first()

        if not app:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="无权访问该执行记录"
            )

        # 获取节点执行记录
        node_executions = service.node_execution_repo.get_by_execution_id(execution.id)

        return success(
            data={
                "execution": WorkflowExecution.model_validate(execution),
                "node_executions": [
                    WorkflowNodeExecution.model_validate(ne) for ne in node_executions
                ]
            }
        )

    except Exception as e:
        logger.error(f"获取工作流执行详情异常: {e}", exc_info=True)
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg=f"获取工作流执行详情失败: {str(e)}"
        )



# ==================== 工作流执行 ====================

@router.post("/{app_id}/workflow/run")
async def run_workflow(
    app_id: Annotated[uuid.UUID, Path(description="应用 ID")],
    request: WorkflowExecutionRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkflowService, Depends(get_workflow_service)]
):
    """执行工作流

    执行工作流并返回结果。支持流式和非流式两种模式。

    **非流式模式**：等待工作流执行完成后返回完整结果。

    **流式模式**：实时返回执行过程中的事件（节点开始、节点完成、工作流完成等）。
    """
    try:
        # 验证应用是否存在且属于当前工作空间
        app = db.query(App).filter(
            App.id == app_id,
            App.workspace_id == current_user.current_workspace_id,
            App.is_active == True
        ).first()

        if not app:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="应用不存在或无权访问"
            )

        # 验证应用类型
        if app.type != "workflow":
            return fail(
                code=BizCode.INVALID_PARAMETER,
                msg=f"应用类型必须为 workflow，当前为 {app.type}"
            )

        # 准备输入数据
        input_data = {
            "message": request.message or "",
            "variables": request.variables
        }

        # 执行工作流

        if request.stream:
            # 流式执行
            from fastapi.responses import StreamingResponse
            import json

            async def event_generator():
                """生成 SSE 事件"""
                try:
                    async for event in await service.run_workflow(
                        app_id=app_id,
                        input_data=input_data,
                        triggered_by=current_user.id,
                        conversation_id=uuid.UUID(request.conversation_id) if request.conversation_id else None,
                        stream=True
                    ):
                        # 转换为 SSE 格式
                        yield f"data: {json.dumps(event)}\n\n"
                except Exception as e:
                    logger.error(f"流式执行异常: {e}", exc_info=True)
                    error_event = {
                        "type": "error",
                        "error": str(e)
                    }
                    yield f"data: {json.dumps(error_event)}\n\n"

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream"
            )
        else:
            # 非流式执行
            result = await service.run_workflow(
                app_id=app_id,
                input_data=input_data,
                triggered_by=current_user.id,
                conversation_id=uuid.UUID(request.conversation_id) if request.conversation_id else None,
                stream=False
            )

            return success(
                data=WorkflowExecutionResponse(
                    execution_id=result["execution_id"],
                    status=result["status"],
                    output=result.get("output"),
                    output_data=result.get("output_data"),
                    error_message=result.get("error_message"),
                    elapsed_time=result.get("elapsed_time"),
                    token_usage=result.get("token_usage")
                ),
                msg="工作流执行完成"
            )

    except BusinessException as e:
        logger.warning(f"执行工作流失败: {e.message}")
        return fail(code=e.error_code, msg=e.message)
    except Exception as e:
        logger.error(f"执行工作流异常: {e}", exc_info=True)
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg=f"执行工作流失败: {str(e)}"
        )


@router.post("/workflow/executions/{execution_id}/cancel")
async def cancel_workflow_execution(
    execution_id: Annotated[str, Path(description="执行 ID")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkflowService, Depends(get_workflow_service)]
):
    """取消工作流执行

    取消正在运行的工作流执行。

    **注意**：当前版本仅更新状态为 cancelled，实际的执行取消功能待实现。
    """
    try:
        # 获取执行记录
        execution = service.get_execution(execution_id)

        if not execution:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="执行记录不存在"
            )

        # 验证应用是否属于当前工作空间
        app = db.query(App).filter(
            App.id == execution.app_id,
            App.workspace_id == current_user.current_workspace_id,
            App.is_active == True
        ).first()

        if not app:
            return fail(
                code=BizCode.NOT_FOUND,
                msg="无权访问该执行记录"
            )

        # 检查执行状态
        if execution.status not in ["pending", "running"]:
            return fail(
                code=BizCode.INVALID_PARAMETER,
                msg=f"无法取消状态为 {execution.status} 的执行"
            )

        # 更新状态为 cancelled
        service.update_execution_status(execution_id, "cancelled")

        return success(msg="工作流执行已取消")

    except BusinessException as e:
        logger.warning(f"取消工作流执行失败: {e.message}")
        return fail(code=e.error_code, msg=e.message)
    except Exception as e:
        logger.error(f"取消工作流执行异常: {e}", exc_info=True)
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg=f"取消工作流执行失败: {str(e)}"
        )
