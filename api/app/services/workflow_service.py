"""
工作流服务层
"""
import datetime
import logging
import uuid
from typing import Any, Annotated, Optional

import yaml
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.workflow.adapters.registry import PlatformAdapterRegistry
from app.core.workflow.executor import execute_workflow, execute_workflow_stream
from app.core.workflow.nodes.enums import NodeType
from app.core.workflow.validator import validate_workflow_config
from app.core.workflow.variable.base_variable import FileObject
from app.db import get_db
from app.models import App
from app.models.workflow_model import WorkflowConfig, WorkflowExecution
from app.repositories import knowledge_repository
from app.repositories.workflow_repository import (
    WorkflowConfigRepository,
    WorkflowExecutionRepository,
    WorkflowNodeExecutionRepository
)
from app.schemas import DraftRunRequest, FileInput
from app.services.conversation_service import ConversationService
from app.services.multi_agent_service import convert_uuids_to_str
from app.services.multimodal_service import MultimodalService
from app.services.workspace_service import get_workspace_storage_type_without_auth

logger = logging.getLogger(__name__)


class WorkflowService:
    """工作流服务"""

    def __init__(self, db: Session):
        self.db = db
        self.config_repo = WorkflowConfigRepository(db)
        self.execution_repo = WorkflowExecutionRepository(db)
        self.node_execution_repo = WorkflowNodeExecutionRepository(db)
        self.conversation_service = ConversationService(db)
        self.multimodal_service = MultimodalService(db)

        self.registry = PlatformAdapterRegistry

    # ==================== 配置管理 ====================

    def create_workflow_config(
            self,
            app_id: uuid.UUID,
            nodes: list[dict[str, Any]],
            edges: list[dict[str, Any]],
            variables: list[dict[str, Any]] | None = None,
            execution_config: dict[str, Any] | None = None,
            features: dict[str, Any] | None = None,
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
            features: 功能特性
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
            "features": features or {},
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
            features=features,
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
        config.is_active = False
        logger.info(f"删除工作流配置成功: app_id={app_id}, config_id={config.id}")
        return True

    def export_workflow_dsl(self, app_id: uuid.UUID):
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        app: App = config.app
        dsl_info = {
            "app": {
                "name": app.name,
                "description": app.description,
                "icon": app.icon,
                "icon_type": app.icon_type
            },
            "workflow": {
                "variables": config.variables,
                "edges": config.edges,
                "nodes": config.nodes,
                "execution_config": config.execution_config,
                "triggers": config.triggers
            }
        }
        return yaml.dump(dsl_info, default_flow_style=False, allow_unicode=True)

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
            release_id: uuid.UUID | None = None,
            triggered_by: uuid.UUID | None = None,
            conversation_id: uuid.UUID | None = None,
            input_data: dict[str, Any] | None = None
    ) -> WorkflowExecution:
        """创建工作流执行记录

        Args:
            release_id: 应用发布 ID
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
            release_id=release_id,
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
            token_usage: int | None = None,
            output_data: dict[str, Any] | None = None,
            error_message: str | None = None,
            error_node_id: str | None = None
    ) -> WorkflowExecution:
        """更新执行状态

        Args:
            execution_id: 执行 ID
            status: 状态
            token_usage: token消耗
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
        if token_usage is not None:
            execution.token_usage = token_usage
        if output_data is not None:
            execution.output_data = convert_uuids_to_str(output_data)
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

    async def _handle_file_input(self, files: list[FileInput]):
        if not files:
            return []

        files_struct = []
        for file in files:
            files_struct.append(
                FileObject(
                    type=file.type,
                    url=await self.multimodal_service.get_file_url(file),
                    transfer_method=file.transfer_method,
                    file_id=str(file.upload_file_id) if file.upload_file_id else None,
                    origin_file_type=file.file_type,
                    is_file=True
                ).model_dump()
            )
        return files_struct

    @staticmethod
    def _map_public_event(event: dict) -> dict | None:
        """
        Map internal workflow events to public-facing event formats.

        Purpose:
        - Hide internal execution details
        - Expose a stable and simplified public event schema
        - Filter out non-public events
        - Maintain backward compatibility when possible

        Args:
            event (dict): Internal event object, e.g.:
                {
                    "event": "workflow_start",
                    "data": {...}
                }

        Returns:
            dict | None:
                - Returns the mapped public event
                - Returns None if the event should not be exposed
        """
        event_type = event.get("event")
        payload = event.get("data")
        match event_type:
            case "workflow_start":
                return {
                    "event": "start",
                    "data": {
                        "conversation_id": payload.get("conversation_id"),
                        "message_id": payload.get("message_id")
                    }
                }
            case "workflow_end":
                return {
                    "event": "end",
                    "data": {
                        "elapsed_time": payload.get("elapsed_time"),
                        "message_length": len(payload.get("output", "")),
                        "error": payload.get("error", "")
                    }
                }
            case "node_start" | "node_end" | "node_error" | "cycle_item":
                return None
            case _:
                return event

    def _emit(self, public: bool, internal_event: dict):
        """
        Unified event emission entry.

        Args:
            public (bool):
                - True  -> Emit mapped public event
                - False -> Emit raw internal event

            internal_event (dict):
                The original internal event object

        Returns:
            dict | None:
                - The mapped event
                - Or None if the event is filtered out
        """
        if public:
            mapped = self._map_public_event(internal_event)
        else:
            mapped = internal_event
        return mapped

    def _get_memory_store_info(self, workspace_id: uuid.UUID) -> tuple[str, str]:
        storage_type = get_workspace_storage_type_without_auth(self.db, workspace_id)
        user_rag_memory_id = ""
        # 如果 storage_type 为 None，使用默认值 'neo4j'
        if not storage_type:
            storage_type = 'neo4j'
            logger.warning(
                f"Storage type not set for workspace {workspace_id}, using default: neo4j"
            )
        if storage_type == "rag":
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=self.db,
                name="USER_RAG_MERORY",
                workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)
            else:
                logger.warning(
                    f"No knowledge base named 'USER_RAG_MEMORY' found, "
                    f"workspace_id: {workspace_id}, will use neo4j storage"
                )
                storage_type = 'neo4j'
        return storage_type, user_rag_memory_id

    def _get_history_info(self, conversation_id: uuid.UUID) -> tuple[dict, list] | None:
        executions = self.execution_repo.get_by_conversation_id(
            conversation_id=conversation_id,
            status="completed",
            limit_count=1
        )

        if executions:
            last_state = executions[0].output_data
            if isinstance(last_state, dict):
                variables = last_state.get("variables", {})
                conv_vars = variables.get("conv", {})
                # input_data["conv"] = conv_vars
                # input_data["conv_messages"] = last_state.get("messages") or []
                conv_messages = last_state.get("messages") or []
                return conv_vars, conv_messages
        return None

    # ==================== 工作流执行 ====================

    async def run(
            self,
            app_id: uuid.UUID,
            payload: DraftRunRequest,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            release_id: uuid.UUID | None = None,
    ):
        """运行工作流

        Args:
            release_id: 发布 ID
            workspace_id:工作空间 ID
            config: 配置
            payload:
            app_id: 应用 ID

        Returns:
            执行结果（非流式）

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

        feature_configs = config.features or {}
        self._validate_file_upload(feature_configs, payload.files)

        input_data = {
            "message": payload.message, "variables": payload.variables,
            "conversation_id": payload.conversation_id,
            "files": [file.model_dump(mode='json') for file in payload.files]
        }

        # 转换 conversation_id 为 UUID
        conversation_id_uuid = uuid.UUID(payload.conversation_id) if payload.conversation_id else None

        # 2. 创建执行记录
        execution = self.create_execution(
            workflow_config_id=config.id,
            app_id=app_id,
            trigger_type="manual",
            triggered_by=None,
            conversation_id=conversation_id_uuid,
            input_data=input_data,
            release_id=release_id,
        )

        # 3. 构建工作流配置字典
        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config
        }

        try:
            files = await self._handle_file_input(payload.files)
            storage_type, user_rag_memory_id = self._get_memory_store_info(workspace_id)
            input_data["files"] = files
            message_id = uuid.uuid4()
            # 更新状态为运行中
            self.update_execution_status(execution.execution_id, "running")

            history = self._get_history_info(conversation_id_uuid)
            if history:
                conv_vars, conv_messages = history
                input_data["conv"] = conv_vars
                input_data["conv_messages"] = conv_messages
            init_message_length = len(input_data.get("conv_messages", []))

            # 新会话时写入开场白
            is_new_conversation = init_message_length == 0
            if is_new_conversation:
                opening_cfg = feature_configs.get("opening_statement", {})
                if isinstance(opening_cfg, dict) and opening_cfg.get("enabled") and opening_cfg.get("statement"):
                    statement = opening_cfg["statement"]
                    suggested_questions = opening_cfg.get("suggested_questions", [])
                    if payload.variables:
                        for var_name, var_value in payload.variables.items():
                            statement = statement.replace(f"{{{{{var_name}}}}}", str(var_value))
                    self.conversation_service.add_message(
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=statement,
                        meta_data={"suggested_questions": suggested_questions}
                    )
                    # 注入到 conv_messages，让 LLM 感知开场白
                    input_data["conv_messages"] = [{"role": "assistant", "content": statement}]
                    init_message_length = 1

            result = await execute_workflow(
                workflow_config=workflow_config_dict,
                input_data=input_data,
                execution_id=execution.execution_id,
                workspace_id=str(workspace_id),
                user_id=payload.user_id,
                memory_storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id
            )
            # 更新执行结果
            if result.get("status") == "completed":
                token_usage = result.get("token_usage", {}) or {}

                final_messages = result.get("messages", [])[init_message_length:]
                human_message = ""
                assistant_message = ""
                human_meta = {
                    "files": []
                }
                for message in final_messages:
                    if message["role"] == "user":
                        if isinstance(message["content"], str):
                            human_message += message["content"]
                        elif isinstance(message["content"], list):
                            for file in message["content"]:
                                human_meta["files"].append({
                                    "type": file.get("type"),
                                    "url": file.get("url")
                                })
                    if message["role"] == "assistant":
                        assistant_message = message["content"]
                self.conversation_service.add_message(
                    conversation_id=conversation_id_uuid,
                    role="user",
                    content=human_message,
                    meta_data=human_meta
                )
                self.conversation_service.add_message(
                    message_id=message_id,
                    conversation_id=conversation_id_uuid,
                    role="assistant",
                    content=assistant_message,
                    meta_data={"usage": token_usage, "audio_url": None}
                )
                self.update_execution_status(
                    execution.execution_id,
                    "completed",
                    output_data=result,
                    token_usage=token_usage.get("total_tokens", None)
                )

                logger.info(f"Workflow Run Success, "
                            f"execution_id: {execution.execution_id}, message count: {len(final_messages)}")
            else:
                self.update_execution_status(
                    execution.execution_id,
                    "failed",
                    error_message=result.get("error")
                )
                logger.error(f"Workflow Run Failed, execution_id: {execution.execution_id},"
                             f" error: {result.get('error')}")

            # 过滤 citations
            citations = result.get("citations", [])
            citation_cfg = feature_configs.get("citation", {})
            filtered_citations = (
                citations if isinstance(citation_cfg, dict) and citation_cfg.get("enabled") else []
            )

            # 返回增强的响应结构
            return {
                "execution_id": execution.execution_id,
                "status": result.get("status"),
                # "variables": result.get("variables"),
                # "messages": result.get("messages"),
                "output": result.get("output"),  # 最终输出（字符串）
                "message": result.get("output"),  # 最终输出（字符串）
                "message_id": str(message_id),
                # "output_data": result.get("node_outputs", {}),  # 所有节点输出（详细数据）
                "conversation_id": result.get("conversation_id"),  # 所有节点输出（详细数据）payload.,  # 会话 ID
                "error_message": result.get("error"),
                "elapsed_time": result.get("elapsed_time"),
                "token_usage": result.get("token_usage"),
                "citations": filtered_citations,
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
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            release_id: Optional[uuid.UUID] = None,
            public: bool = False
    ):
        """运行工作流（流式）

        Args:
            release_id: 发布id
            workspace_id:
            app_id: 应用 ID
            payload: 请求对象（包含 message, variables, conversation_id 等）
            config: 存储类型（可选）
            public: 是否发布

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
        feature_configs = config.features or {}
        self._validate_file_upload(feature_configs, payload.files)

        input_data = {
            "message": payload.message, "variables": payload.variables,
            "conversation_id": payload.conversation_id,
            "files": [file.model_dump(mode='json') for file in payload.files]
        }

        # 转换 conversation_id 为 UUID
        conversation_id_uuid = uuid.UUID(payload.conversation_id) if payload.conversation_id else None

        # 2. 创建执行记录
        execution = self.create_execution(
            workflow_config_id=config.id,
            app_id=app_id,
            trigger_type="manual",
            triggered_by=None,
            conversation_id=conversation_id_uuid,
            input_data=input_data,
            release_id=release_id,
        )

        # 3. 构建工作流配置字典
        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config
        }

        try:
            files = await self._handle_file_input(payload.files)
            storage_type, user_rag_memory_id = self._get_memory_store_info(workspace_id)
            input_data["files"] = files
            self.update_execution_status(execution.execution_id, "running")
            history = self._get_history_info(conversation_id_uuid)
            if history:
                conv_vars, conv_messages = history
                input_data["conv"] = conv_vars
                input_data["conv_messages"] = conv_messages
            init_message_length = len(input_data.get("conv_messages", []))
            message_id = uuid.uuid4()

            # 新会话时写入开场白
            is_new_conversation = init_message_length == 0
            if is_new_conversation:
                opening_cfg = feature_configs.get("opening_statement", {})
                if isinstance(opening_cfg, dict) and opening_cfg.get("enabled") and opening_cfg.get("statement"):
                    statement = opening_cfg["statement"]
                    suggested_questions = opening_cfg.get("suggested_questions", [])
                    if payload.variables:
                        for var_name, var_value in payload.variables.items():
                            statement = statement.replace(f"{{{{{var_name}}}}}", str(var_value))
                    self.conversation_service.add_message(
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=statement,
                        meta_data={"suggested_questions": suggested_questions}
                    )
                    # 注入到 conv_messages，让 LLM 感知开场白
                    input_data["conv_messages"] = [{"role": "assistant", "content": statement}]
                    init_message_length = 1

            async for event in execute_workflow_stream(
                    workflow_config=workflow_config_dict,
                    input_data=input_data,
                    execution_id=execution.execution_id,
                    workspace_id=str(workspace_id),
                    user_id=payload.user_id,
                    memory_storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id
            ):
                if event.get("event") == "workflow_end":
                    status = event.get("data", {}).get("status")
                    token_usage = event.get("data", {}).get("token_usage", {}) or {}
                    if status == "completed":
                        final_messages = event.get("data", {}).get("messages", [])[init_message_length:]
                        human_message = ""
                        assistant_message = ""
                        human_meta = {
                            "files": []
                        }
                        for message in final_messages:
                            if message["role"] == "user":
                                if isinstance(message["content"], str):
                                    human_message += message["content"]
                                elif isinstance(message["content"], list):
                                    for file in message["content"]:
                                        human_meta["files"].append({
                                            "type": file.get("type"),
                                            "url": file.get("url")
                                        })
                            if message["role"] == "assistant":
                                assistant_message = message["content"]
                        self.conversation_service.add_message(
                            conversation_id=conversation_id_uuid,
                            role="user",
                            content=human_message,
                            meta_data=human_meta
                        )
                        self.conversation_service.add_message(
                            message_id=message_id,
                            conversation_id=conversation_id_uuid,
                            role="assistant",
                            content=assistant_message,
                            meta_data={"usage": token_usage, "audio_url": None}
                        )
                        self.update_execution_status(
                            execution.execution_id,
                            "completed",
                            output_data=event.get("data"),
                            token_usage=token_usage.get("total_tokens", None)
                        )
                        # 注入 citations 到 workflow_end 事件
                        citations = event.get("data", {}).get("citations", [])
                        citation_cfg = feature_configs.get("citation", {})
                        filtered_citations = (
                            citations if isinstance(citation_cfg, dict) and citation_cfg.get("enabled") else []
                        )
                        event.setdefault("data", {})["citations"] = filtered_citations
                        logger.info(f"Workflow Run Success, "
                                    f"execution_id: {execution.execution_id}, message count: {len(final_messages)}")
                    elif status == "failed":
                        self.update_execution_status(
                            execution.execution_id,
                            "failed",
                            output_data=event.get("data")
                        )
                    else:
                        logger.error(f"unexpect workflow run status, status: {status}")
                elif event.get("event") == "workflow_start":
                    event["data"]["message_id"] = str(message_id)
                event = self._emit(public, event)
                if event:
                    yield event

        except Exception as e:
            logger.error(
                f"Workflow streaming execution failed: execution_id={execution.execution_id}, error={e}",
                exc_info=True
            )
            self.update_execution_status(
                execution.execution_id,
                "failed",
                error_message=str(e)
            )
            # 发送错误事件
            yield {
                "event": "error",
                "data": {
                    "execution_id": execution.execution_id,
                    "error": str(e)
                }
            }

    @staticmethod
    def get_start_node_variables(config: dict) -> list:
        nodes = config.get("nodes", [])
        for node in nodes:
            if node.get("type") == NodeType.START:
                return node.get("config", {}).get("variables", [])
        raise BusinessException("workflow config error - start node not found")

    @staticmethod
    def is_memory_enable(config: dict) -> bool:
        nodes = config.get("nodes", [])
        for node in nodes:
            if node.get("type") in [NodeType.MEMORY_READ, NodeType.MEMORY_WRITE]:
                return True
        return False

    @staticmethod
    def _validate_file_upload(
            features_config: dict[str, Any],
            files: Optional[list[FileInput]]
    ) -> None:
        """校验上传文件是否符合 file_upload 配置"""
        if not files:
            return
        fu = features_config.get("file_upload")
        if fu is None:
            return
        if not (isinstance(fu, dict) and fu.get("enabled")):
            raise BusinessException(
                "The application does not have file upload functionality enabled",
                BizCode.BAD_REQUEST
            )
        max_count = fu.get("max_file_count", 5)
        if len(files) > max_count:
            raise BusinessException(
                f"File count exceeds limit (maximum {max_count} files)",
                BizCode.BAD_REQUEST
            )

        # 校验传输方式
        allowed_methods = fu.get("allowed_transfer_methods", ["local_file", "remote_url"])
        for f in files:
            if f.transfer_method.value not in allowed_methods:
                raise BusinessException(
                    f"Unsupport file transfer method：{f.transfer_method.value},"
                    f"allowed method:{', '.join(allowed_methods)}",
                    BizCode.BAD_REQUEST
                )

        # 各类型对应的开关和大小限制配置键
        type_cfg = {
            "image": ("image_enabled", "image_max_size_mb", 20, "image"),
            "audio": ("audio_enabled", "audio_max_size_mb", 50, "audio"),
            "document": ("document_enabled", "document_max_size_mb", 100, "document"),
            "video": ("video_enabled", "video_max_size_mb", 500, "video"),
        }

        for f in files:
            ftype = str(f.type)  # 如 "image", "audio", "document", "video"
            cfg = type_cfg.get(ftype)
            if cfg is None:
                continue
            enabled_key, size_key, default_max_mb, label = cfg

            # 校验类型开关
            if not fu.get(enabled_key):
                raise BusinessException(
                    f"The application has not enabled {label} file upload",
                    BizCode.BAD_REQUEST
                )

            # 校验文件大小（仅当内容已加载时）
            content = f.get_content()
            if content is not None:
                max_mb = fu.get(size_key, default_max_mb)
                size_mb = len(content) / (1024 * 1024)
                if size_mb > max_mb:
                    raise BusinessException(
                        f"{label} File size exceeds the limit (maximum {max_mb} MB, current {size_mb:.1f} MB)",
                        BizCode.BAD_REQUEST
                    )


# ==================== 依赖注入函数 ====================

def get_workflow_service(
        db: Annotated[Session, Depends(get_db)]
) -> WorkflowService:
    """获取工作流服务（依赖注入）"""
    return WorkflowService(db)
