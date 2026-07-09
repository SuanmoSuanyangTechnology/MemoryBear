import asyncio
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.tool.config import ToolNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_async_db_context, get_db_read
from app.models.workspace_model import Workspace
from app.services.tool_service import ToolService
from app.models.tool_model import ToolType

logger = logging.getLogger(__name__)

TEMPLATE_PATTERN = re.compile(r"\{\{.*?}}")
PURE_VARIABLE_PATTERN = re.compile(r"^\{\{\s*([\w.]+)\s*}}$")


class ToolNode(BaseNode):
    """工具节点"""

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: ToolNodeConfig | None = None
        self._process: dict = {}

    def _extract_extra_fields(self, business_result: Any) -> dict:
        return {"process": self._process}

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "data": VariableType.STRING,
            "execution_time": VariableType.NUMBER
        }

    @staticmethod
    def _normalize_uuid(value: Any) -> uuid.UUID | None:
        if value in (None, ""):
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

    async def _resolve_tenant_id_async(self, variable_pool: VariablePool) -> uuid.UUID | None:
        tenant_id = self.get_variable("sys.tenant_id", variable_pool, strict=False)
        if tenant_id:
            return self._normalize_uuid(tenant_id)

        workspace_id = self.get_variable("sys.workspace_id", variable_pool, strict=False)
        workspace_uuid = self._normalize_uuid(workspace_id)
        if not workspace_uuid:
            return None

        async with get_async_db_context() as db:
            return (
                await db.execute(
                    select(Workspace.tenant_id).where(Workspace.id == workspace_uuid)
                )
            ).scalar_one_or_none()

    async def _execute_workflow_tool_legacy_async(
            self,
            *,
            tenant_id: uuid.UUID,
            user_id: uuid.UUID | None,
            workspace_id: uuid.UUID | None,
            rendered_parameters: dict[str, Any],
    ):
        def _run():
            with get_db_read() as db:
                tool_service = ToolService(db)
                return asyncio.run(
                    tool_service.execute_tool(
                        tool_id=str(self.typed_config.tool_id),
                        parameters=rendered_parameters,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                )

        # ponytail: workflow tools still ride sync WorkflowService internals; keep fallback isolated off the async hot path.
        return await asyncio.to_thread(_run)

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """执行工具"""
        self.typed_config = ToolNodeConfig(**self.config)
        # 获取租户ID和用户ID
        tenant_id = await self._resolve_tenant_id_async(variable_pool)
        user_id = self._normalize_uuid(self.get_variable("sys.user_id", variable_pool))
        workspace_id = self._normalize_uuid(self.get_variable("sys.workspace_id", variable_pool))

        if not tenant_id:
            logger.error(f"节点 {self.node_id} 缺少租户ID")
            raise ValueError("缺少租户ID")

        # 渲染工具参数
        rendered_parameters = {}
        for param_name, param_template in self.typed_config.tool_parameters.items():
            if isinstance(param_template, str):
                pure_match = PURE_VARIABLE_PATTERN.match(param_template)
                if pure_match:
                    # 纯单变量引用直接取原始值，保留 int/bool/float 等类型
                    rendered_value = self.get_variable(pure_match.group(1), variable_pool, strict=False)
                    if rendered_value is None:
                        rendered_value = self._render_template(param_template, variable_pool)
                elif TEMPLATE_PATTERN.search(param_template):
                    try:
                        rendered_value = self._render_template(param_template, variable_pool)
                    except Exception as e:
                        raise ValueError(f"模板渲染失败：参数 {param_name} 的模板 {param_template} 解析错误") from e
                else:
                    rendered_value = param_template
            else:
                rendered_value = param_template
            rendered_parameters[param_name] = rendered_value

        logger.info(f"节点 {self.node_id} 执行工具 {self.typed_config.tool_id}，参数: {rendered_parameters}")
        self._process = {"tool_id": str(self.typed_config.tool_id), "parameters": rendered_parameters}

        async with get_async_db_context() as db:
            tool_service = ToolService(db)
            tool_config = await tool_service.get_tool_config_async(str(self.typed_config.tool_id), tenant_id)
            if not tool_config:
                raise ValueError(f"工具不存在或未激活: {self.typed_config.tool_id}")

            if tool_config.tool_type == ToolType.WORKFLOW.value:
                result = await self._execute_workflow_tool_legacy_async(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    rendered_parameters=rendered_parameters,
                )
            else:
                tool_instance = await tool_service.get_tool_instance_async(str(self.typed_config.tool_id), tenant_id)
                # MCP 工具：将 operation 映射为 tool_name，其余参数包装进 arguments
                if tool_instance and tool_instance.tool_type == ToolType.MCP:
                    operation = rendered_parameters.pop("operation", None)
                    if operation:
                        old_params = rendered_parameters
                        rendered_parameters = {
                            "tool_name": operation,
                            "arguments": old_params
                        }

                result = await tool_service.execute_tool(
                    tool_id=str(self.typed_config.tool_id),
                    parameters=rendered_parameters,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                )

        if result.success:
            logger.info(f"节点 {self.node_id} 工具执行成功")
            self._process["raw_output"] = result.data
            return {
                "data": result.data if isinstance(result.data, str) else json.dumps(result.data, ensure_ascii=False),
                "execution_time": result.execution_time
            }
        else:
            logger.error(f"节点 {self.node_id} 工具执行失败: {result.error}")
            raise ValueError(f"工具执行失败: {result.error if isinstance(result.error, str) else json.dumps(result.error, ensure_ascii=False)}")
