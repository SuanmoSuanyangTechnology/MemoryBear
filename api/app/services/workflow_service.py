"""
工作流服务层
"""
import json
import logging
import uuid
import datetime
from typing import Any, Annotated, AsyncGenerator

from sqlalchemy.orm import Session
from fastapi import Depends

from app.models.workflow_model import WorkflowConfig, WorkflowExecution
from app.repositories.workflow_repository import (
    WorkflowConfigRepository,
    WorkflowExecutionRepository,
    WorkflowNodeExecutionRepository,
    get_workflow_config_repository,
    get_workflow_execution_repository,
    get_workflow_node_execution_repository
)
from app.core.workflow.validator import validate_workflow_config
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.db import get_db
from app.schemas import DraftRunRequest

logger = logging.getLogger(__name__)


class WorkflowService:
    """工作流服务"""

    def __init__(self, db: Session):
        self.db = db
        self.config_repo = WorkflowConfigRepository(db)
        self.execution_repo = WorkflowExecutionRepository(db)
        self.node_execution_repo = WorkflowNodeExecutionRepository(db)

    # ==================== 配置管理 ====================

    def create_workflow_config(
        self,
        app_id: uuid.UUID,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        variables: list[dict[str, Any]] | None = None,
        execution_config: dict[str, Any] | None = None,
        triggers: list[dict[str, Any]] | None = None,
        validate: bool = True
    ) -> WorkflowConfig:
        """创建工作流配置

        Args:
            app_id: 应用 ID
            nodes: 节点列表
            edges: 边列表
            variables: 变量列表
            execution_config: 执行配置
            triggers: 触发器列表
            validate: 是否验证配置

        Returns:
            工作流配置

        Raises:
            BusinessException: 配置无效时抛出
        """
        # 构建配置字典
        config_dict = {
            "nodes": nodes,
            "edges": edges,
            "variables": variables or [],
            "execution_config": execution_config or {},
            "triggers": triggers or []
        }

        # 验证配置
        if validate:
            is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
            if not is_valid:
                logger.warning(f"工作流配置验证失败: {errors}")
                raise BusinessException(
                    code=BizCode.INVALID_PARAMETER,
                    message=f"工作流配置无效: {'; '.join(errors)}"
                )

        # 创建或更新配置
        config = self.config_repo.create_or_update(
            app_id=app_id,
            nodes=nodes,
            edges=edges,
            variables=variables,
            execution_config=execution_config,
            triggers=triggers
        )

        logger.info(f"创建工作流配置成功: app_id={app_id}, config_id={config.id}")
        return config

    def get_workflow_config(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """获取工作流配置

        Args:
            app_id: 应用 ID

        Returns:
            工作流配置或 None
        """
        return self.config_repo.get_by_app_id(app_id)

    def update_workflow_config(
        self,
        app_id: uuid.UUID,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        variables: list[dict[str, Any]] | None = None,
        execution_config: dict[str, Any] | None = None,
        triggers: list[dict[str, Any]] | None = None,
        validate: bool = True
    ) -> WorkflowConfig:
        """更新工作流配置

        Args:
            app_id: 应用 ID
            nodes: 节点列表
            edges: 边列表
            variables: 变量列表
            execution_config: 执行配置
            triggers: 触发器列表
            validate: 是否验证配置

        Returns:
            工作流配置

        Raises:
            BusinessException: 配置不存在或无效时抛出
        """
        # 获取现有配置
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        # 合并配置
        updated_nodes = nodes if nodes is not None else config.nodes
        updated_edges = edges if edges is not None else config.edges
        updated_variables = variables if variables is not None else config.variables
        updated_execution_config = execution_config if execution_config is not None else config.execution_config
        updated_triggers = triggers if triggers is not None else config.triggers

        # 构建配置字典
        config_dict = {
            "nodes": updated_nodes,
            "edges": updated_edges,
            "variables": updated_variables,
            "execution_config": updated_execution_config,
            "triggers": updated_triggers
        }

        # 验证配置
        if validate:
            is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
            if not is_valid:
                logger.warning(f"工作流配置验证失败: {errors}")
                raise BusinessException(
                    code=BizCode.INVALID_PARAMETER,
                    message=f"工作流配置无效: {'; '.join(errors)}"
                )

        # 更新配置
        config = self.config_repo.create_or_update(
            app_id=app_id,
            nodes=updated_nodes,
            edges=updated_edges,
            variables=updated_variables,
            execution_config=updated_execution_config,
            triggers=updated_triggers
        )

        logger.info(f"更新工作流配置成功: app_id={app_id}, config_id={config.id}")
        return config

    def delete_workflow_config(self, app_id: uuid.UUID) -> bool:
        """删除工作流配置

        Args:
            app_id: 应用 ID

        Returns:
            是否删除成功
        """
        config = self.get_workflow_config(app_id)
        if not config:
            return False

        self.config_repo.delete(config.id)
        logger.info(f"删除工作流配置成功: app_id={app_id}, config_id={config.id}")
        return True

    def check_config(self, app_id: uuid.UUID) -> WorkflowConfig:
        """检查工作流配置的完整性

        Args:
            app_id: 应用 ID

        Raises:
            BusinessException: 配置不完整或不存在时抛出
        """

        # 1. 检查多智能体配置是否存在
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                "工作流配置不存在，无法运行",
                BizCode.CONFIG_MISSING
            )
        # validator 现在支持直接接受 Pydantic 模型
        is_valid, errors = validate_workflow_config(config, for_publish=False)
        if not is_valid:
            logger.warning(f"工作流配置验证失败: {errors}")
            raise BusinessException(
                code=BizCode.INVALID_PARAMETER,
                message=f"工作流配置无效: {'; '.join(errors)}"
            )
        return config

    def validate_workflow_config_for_publish(
        self,
        app_id: uuid.UUID
    ) -> tuple[bool, list[str]]:
        """验证工作流配置是否可以发布

        Args:
            app_id: 应用 ID

        Returns:
            (is_valid, errors): 是否有效和错误列表

        Raises:
            BusinessException: 配置不存在时抛出
        """
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config,
            "triggers": config.triggers
        }

        return validate_workflow_config(config_dict, for_publish=True)

    # ==================== 执行管理 ====================

    def create_execution(
        self,
        workflow_config_id: uuid.UUID,
        app_id: uuid.UUID,
        trigger_type: str,
        triggered_by: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        input_data: dict[str, Any] | None = None
    ) -> WorkflowExecution:
        """创建工作流执行记录

        Args:
            workflow_config_id: 工作流配置 ID
            app_id: 应用 ID
            trigger_type: 触发类型
            triggered_by: 触发用户 ID
            conversation_id: 会话 ID
            input_data: 输入数据

        Returns:
            执行记录
        """
        # 生成执行 ID
        execution_id = f"exec_{uuid.uuid4().hex[:16]}"

        execution = WorkflowExecution(
            workflow_config_id=workflow_config_id,
            app_id=app_id,
            conversation_id=conversation_id,
            execution_id=execution_id,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            input_data=input_data or {},
            status="pending"
        )

        self.db.add(execution)
        self.db.commit()
        self.db.refresh(execution)

        logger.info(f"创建工作流执行记录: execution_id={execution_id}")
        return execution

    def get_execution(self, execution_id: str) -> WorkflowExecution | None:
        """获取执行记录

        Args:
            execution_id: 执行 ID

        Returns:
            执行记录或 None
        """
        return self.execution_repo.get_by_execution_id(execution_id)

    def get_executions_by_app(
        self,
        app_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0
    ) -> list[WorkflowExecution]:
        """获取应用的执行记录列表

        Args:
            app_id: 应用 ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            执行记录列表
        """
        return self.execution_repo.get_by_app_id(app_id, limit, offset)

    def update_execution_status(
        self,
        execution_id: str,
        status: str,
        output_data: dict[str, Any] | None = None,
        error_message: str | None = None,
        error_node_id: str | None = None
    ) -> WorkflowExecution:
        """更新执行状态

        Args:
            execution_id: 执行 ID
            status: 状态
            output_data: 输出数据
            error_message: 错误信息
            error_node_id: 出错节点 ID

        Returns:
            执行记录

        Raises:
            BusinessException: 执行记录不存在时抛出
        """
        execution = self.get_execution(execution_id)
        if not execution:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"执行记录不存在: execution_id={execution_id}"
            )

        execution.status = status
        if output_data is not None:
            execution.output_data = output_data
        if error_message is not None:
            execution.error_message = error_message
        if error_node_id is not None:
            execution.error_node_id = error_node_id

        # 如果是完成状态，计算耗时
        if status in ["completed", "failed", "cancelled", "timeout"]:
            if not execution.completed_at:
                execution.completed_at = datetime.datetime.now()
                elapsed = (execution.completed_at - execution.started_at).total_seconds()
                execution.elapsed_time = elapsed

        self.db.commit()
        self.db.refresh(execution)

        logger.info(f"更新执行状态: execution_id={execution_id}, status={status}")
        return execution

    def get_execution_statistics(self, app_id: uuid.UUID) -> dict[str, Any]:
        """获取执行统计信息

        Args:
            app_id: 应用 ID

        Returns:
            统计信息
        """
        total = self.execution_repo.count_by_app_id(app_id)
        completed = self.execution_repo.count_by_status(app_id, "completed")
        failed = self.execution_repo.count_by_status(app_id, "failed")
        running = self.execution_repo.count_by_status(app_id, "running")

        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "success_rate": completed / total if total > 0 else 0
        }

    # ==================== 工作流执行 ====================

    async def run(
        self,
        app_id: uuid.UUID,
        payload: DraftRunRequest,
        config: WorkflowConfig
    ):
        """运行工作流

        Args:
            app_id: 应用 ID
            input_data: 输入数据（包含 message 和 variables）
            triggered_by: 触发用户 ID
            conversation_id: 会话 ID（可选）
            stream: 是否流式返回

        Returns:
            执行结果（非流式）或生成器（流式）

        Raises:
            BusinessException: 配置不存在或执行失败时抛出
        """
        # 1. 获取工作流配置
        if not config:
            config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.CONFIG_MISSING,
                message=f"工作流配置不存在: app_id={app_id}"
            )
        input_data = {"message": payload.message, "variables": payload.variables, "conversation_id": payload.conversation_id}

        # 转换 user_id 为 UUID
        triggered_by_uuid = None
        if payload.user_id:
            try:
                triggered_by_uuid = uuid.UUID(payload.user_id)
            except (ValueError, AttributeError):
                logger.warning(f"无效的 user_id 格式: {payload.user_id}")

        # 转换 conversation_id 为 UUID
        conversation_id_uuid = None
        if payload.conversation_id:
            try:
                conversation_id_uuid = uuid.UUID(payload.conversation_id)
            except (ValueError, AttributeError):
                logger.warning(f"无效的 conversation_id 格式: {payload.conversation_id}")

        # 2. 创建执行记录
        execution = self.create_execution(
            workflow_config_id=config.id,
            app_id=app_id,
            trigger_type="manual",
            triggered_by=triggered_by_uuid,
            conversation_id=conversation_id_uuid,
            input_data=input_data
        )

        # 3. 构建工作流配置字典
        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config
        }

        # 4. 获取工作空间 ID（从 app 获取）
        from app.models import App


        # 5. 执行工作流
        from app.core.workflow.executor import execute_workflow, execute_workflow_stream

        try:
            # 更新状态为运行中
            self.update_execution_status(execution.execution_id, "running")

            result = await execute_workflow(
                workflow_config=workflow_config_dict,
                input_data=input_data,
                execution_id=execution.execution_id,
                workspace_id="",
                user_id=payload.user_id
            )

            # 更新执行结果
            if result.get("status") == "completed":
                self.update_execution_status(
                    execution.execution_id,
                    "completed",
                    output_data=result.get("node_outputs", {})
                )
            else:
                self.update_execution_status(
                    execution.execution_id,
                    "failed",
                    error_message=result.get("error")
                )

            # 返回增强的响应结构
            return {
                "execution_id": execution.execution_id,
                "status": result.get("status"),
                "output": result.get("output"),  # 最终输出（字符串）
                "output_data": result.get("node_outputs", {}),  # 所有节点输出（详细数据）
                "conversation_id": result.get("conversation_id"),  # 所有节点输出（详细数据）payload.,  # 会话 ID
                "error_message": result.get("error"),
                "elapsed_time": result.get("elapsed_time"),
                "token_usage": result.get("token_usage")
            }

        except Exception as e:
            logger.error(f"工作流执行失败: execution_id={execution.execution_id}, error={e}", exc_info=True)
            self.update_execution_status(
                execution.execution_id,
                "failed",
                error_message=str(e)
            )
            raise BusinessException(
                code=BizCode.INTERNAL_ERROR,
                message=f"工作流执行失败: {str(e)}"
            )

    async def run_stream(
        self,
        app_id: uuid.UUID,
        payload: DraftRunRequest,
        config: WorkflowConfig
    ):
        """运行工作流（流式）

        Args:
            app_id: 应用 ID
            payload: 请求对象（包含 message, variables, conversation_id 等）
            config: 存储类型（可选）

        Yields:
            SSE 格式的流式事件

        Raises:
            BusinessException: 配置不存在或执行失败时抛出
        """
        # 1. 获取工作流配置
        if not config:
            config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.CONFIG_MISSING,
                message=f"工作流配置不存在: app_id={app_id}"
            )
        input_data = {"message": payload.message, "variables": payload.variables,
                      "conversation_id": payload.conversation_id}

        # 转换 user_id 为 UUID
        triggered_by_uuid = None
        if payload.user_id:
            try:
                triggered_by_uuid = uuid.UUID(payload.user_id)
            except (ValueError, AttributeError):
                logger.warning(f"无效的 user_id 格式: {payload.user_id}")

        # 转换 conversation_id 为 UUID
        conversation_id_uuid = None
        if payload.conversation_id:
            try:
                conversation_id_uuid = uuid.UUID(payload.conversation_id)
            except (ValueError, AttributeError):
                logger.warning(f"无效的 conversation_id 格式: {payload.conversation_id}")

        # 2. 创建执行记录
        execution = self.create_execution(
            workflow_config_id=config.id,
            app_id=app_id,
            trigger_type="manual",
            triggered_by=triggered_by_uuid,
            conversation_id=conversation_id_uuid,
            input_data=input_data
        )

        # 3. 构建工作流配置字典
        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config
        }

        # 4. 获取工作空间 ID（从 app 获取）
        from app.models import App

        # 5. 流式执行工作流
        from app.core.workflow.executor import execute_workflow, execute_workflow_stream

        try:
            # 更新状态为运行中
            self.update_execution_status(execution.execution_id, "running")

            # 发送开始事件
            yield f"data: {json.dumps({'type': 'workflow_start', 'execution_id': execution.execution_id})}\n\n"

            # 调用流式执行
            async for event in self._run_workflow_stream(
                workflow_config=workflow_config_dict,
                input_data=input_data,
                execution_id=execution.execution_id,
                workspace_id="",
                user_id=payload.user_id
            ):
                # 清理事件数据，移除不可序列化的对象
                cleaned_event = self._clean_event_for_json(event)
                # 转换为 SSE 格式
                yield f"data: {json.dumps(cleaned_event)}\n\n"

            # 发送完成事件
            yield f"data: {json.dumps({'type': 'workflow_end', 'execution_id': execution.execution_id})}\n\n"

        except Exception as e:
            logger.error(f"工作流流式执行失败: execution_id={execution.execution_id}, error={e}", exc_info=True)
            self.update_execution_status(
                execution.execution_id,
                "failed",
                error_message=str(e)
            )
            # 发送错误事件
            yield f"data: {json.dumps({'type': 'error', 'execution_id': execution.execution_id, 'error': str(e)})}\n\n"

    async def run_workflow(
        self,
        app_id: uuid.UUID,
        input_data: dict[str, Any],
        triggered_by: uuid.UUID,
        conversation_id: uuid.UUID | None = None,
        stream: bool = False
    ) -> AsyncGenerator | dict:
        """运行工作流

        Args:
            app_id: 应用 ID
            input_data: 输入数据（包含 message 和 variables）
            triggered_by: 触发用户 ID
            conversation_id: 会话 ID（可选）
            stream: 是否流式返回

        Returns:
            执行结果（非流式）或生成器（流式）

        Raises:
            BusinessException: 配置不存在或执行失败时抛出
        """
        # 1. 获取工作流配置
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        # 2. 创建执行记录
        execution = self.create_execution(
            workflow_config_id=config.id,
            app_id=app_id,
            trigger_type="manual",
            triggered_by=triggered_by,
            conversation_id=conversation_id,
            input_data=input_data
        )

        # 3. 构建工作流配置字典
        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config
        }

        # 4. 获取工作空间 ID（从 app 获取）
        from app.models import App
        app = self.db.query(App).filter(App.id == app_id).first()
        if not app:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"应用不存在: app_id={app_id}"
            )

        # 5. 执行工作流
        from app.core.workflow.executor import execute_workflow, execute_workflow_stream

        try:
            # 更新状态为运行中
            self.update_execution_status(execution.execution_id, "running")

            if stream:
                # 流式执行
                return self._run_workflow_stream(
                    workflow_config_dict,
                    input_data,
                    execution.execution_id,
                    str(app.workspace_id),
                    str(triggered_by)
                )
            else:
                # 非流式执行
                result = await execute_workflow(
                    workflow_config=workflow_config_dict,
                    input_data=input_data,
                    execution_id=execution.execution_id,
                    workspace_id=str(app.workspace_id),
                    user_id=str(triggered_by)
                )

                # 更新执行结果
                if result.get("status") == "completed":
                    self.update_execution_status(
                        execution.execution_id,
                        "completed",
                        output_data=result.get("node_outputs", {})
                    )
                else:
                    self.update_execution_status(
                        execution.execution_id,
                        "failed",
                        error_message=result.get("error")
                    )

                # 返回增强的响应结构
                return {
                    "execution_id": execution.execution_id,
                    "status": result.get("status"),
                    "output": result.get("output"),  # 最终输出（字符串）
                    "output_data": result.get("node_outputs", {}),  # 所有节点输出（详细数据）
                    "error_message": result.get("error"),
                    "elapsed_time": result.get("elapsed_time"),
                    "token_usage": result.get("token_usage")
                }

        except Exception as e:
            logger.error(f"工作流执行失败: execution_id={execution.execution_id}, error={e}", exc_info=True)
            self.update_execution_status(
                execution.execution_id,
                "failed",
                error_message=str(e)
            )
            raise BusinessException(
                code=BizCode.INTERNAL_ERROR,
                message=f"工作流执行失败: {str(e)}"
            )

    def _clean_event_for_json(self, event: dict[str, Any]) -> dict[str, Any]:
        """清理事件数据，移除不可序列化的对象

        Args:
            event: 原始事件数据

        Returns:
            可序列化的事件数据
        """
        from langchain_core.messages import BaseMessage

        def clean_value(value):
            """递归清理值"""
            if isinstance(value, BaseMessage):
                # 将 Message 对象转换为字典
                return {
                    "type": value.__class__.__name__,
                    "content": value.content,
                }
            elif isinstance(value, dict):
                return {k: clean_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [clean_value(item) for item in value]
            elif isinstance(value, (str, int, float, bool, type(None))):
                return value
            else:
                # 其他不可序列化的对象转换为字符串
                return str(value)

        return clean_value(event)

    async def _run_workflow_stream(
        self,
        workflow_config: dict[str, Any],
        input_data: dict[str, Any],
        execution_id: str,
        workspace_id: str,
        user_id: str):
        """运行工作流（流式，内部方法）

        Args:
            workflow_config: 工作流配置
            input_data: 输入数据
            execution_id: 执行 ID
            workspace_id: 工作空间 ID
            user_id: 用户 ID

        Yields:
            流式事件
        """
        from app.core.workflow.executor import execute_workflow_stream

        try:
            output_data = {}

            async for event in execute_workflow_stream(
                workflow_config=workflow_config,
                input_data=input_data,
                execution_id=execution_id,
                workspace_id=workspace_id,
                user_id=user_id
            ):
                # 转发事件
                yield event

                # 收集输出数据
                if event.get("type") == "node_complete":
                    node_data = event.get("data", {})
                    node_outputs = node_data.get("node_outputs", {})
                    output_data.update(node_outputs)

                # 处理完成事件
                if event.get("type") == "workflow_complete":
                    self.update_execution_status(
                        execution_id,
                        "completed",
                        output_data=output_data
                    )

                # 处理错误事件
                if event.get("type") == "workflow_error":
                    self.update_execution_status(
                        execution_id,
                        "failed",
                        error_message=event.get("error")
                    )

        except Exception as e:
            logger.error(f"工作流流式执行失败: execution_id={execution_id}, error={e}", exc_info=True)
            self.update_execution_status(
                execution_id,
                "failed",
                error_message=str(e)
            )
            yield {
                "type": "workflow_error",
                "execution_id": execution_id,
                "error": str(e)
            }


# ==================== 依赖注入函数 ====================

def get_workflow_service(
    db: Annotated[Session, Depends(get_db)]
) -> WorkflowService:
    """获取工作流服务（依赖注入）"""
    return WorkflowService(db)
