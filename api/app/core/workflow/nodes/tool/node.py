import json
import logging
import re
import uuid
from typing import Any

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.tool.config import ToolNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_db_read
from app.services.tool_service import ToolService

logger = logging.getLogger(__name__)

TEMPLATE_PATTERN = re.compile(r"\{\{.*?}}")


class ToolNode(BaseNode):
    """工具节点"""

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: ToolNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "data": VariableType.STRING,
            "execution_time": VariableType.NUMBER
        }

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """执行工具"""
        self.typed_config = ToolNodeConfig(**self.config)
        # 获取租户ID和用户ID
        tenant_id = self.get_variable("sys.tenant_id", variable_pool, strict=False)
        user_id = self.get_variable("sys.user_id", variable_pool)
        workspace_id = self.get_variable("sys.workspace_id", variable_pool)

        # 如果没有租户ID，尝试从工作流ID获取
        if not tenant_id:
            if workspace_id:
                from app.repositories.tool_repository import ToolRepository
                with get_db_read() as db:
                    tenant_id = ToolRepository.get_tenant_id_by_workspace_id(db, workspace_id)

        if not tenant_id:
            logger.error(f"节点 {self.node_id} 缺少租户ID")
            raise ValueError("缺少租户ID")

        # 渲染工具参数
        rendered_parameters = {}
        for param_name, param_template in self.typed_config.tool_parameters.items():
            if isinstance(param_template, str) and TEMPLATE_PATTERN.search(param_template):
                try:
                    rendered_value = self._render_template(param_template, variable_pool)
                except Exception as e:
                    raise ValueError(f"模板渲染失败：参数 {param_name} 的模板 {param_template} 解析错误") from e
            else:
                # 非模板参数（数字/布尔/普通字符串）直接保留原值
                rendered_value = param_template
            rendered_parameters[param_name] = rendered_value

        logger.info(f"节点 {self.node_id} 执行工具 {self.typed_config.tool_id}，参数: {rendered_parameters}")

        # 执行工具
        with get_db_read() as db:
            tool_service = ToolService(db)
            result = await tool_service.execute_tool(
                tool_id=self.typed_config.tool_id,
                parameters=rendered_parameters,
                tenant_id=tenant_id,
                user_id=uuid.UUID(user_id),
                workspace_id=uuid.UUID(workspace_id)
            )

        if result.success:
            logger.info(f"节点 {self.node_id} 工具执行成功")
            return {
                "data": result.data if isinstance(result.data, str) else json.dumps(result.data, ensure_ascii=False),
                "execution_time": result.execution_time
            }
        else:
            logger.error(f"节点 {self.node_id} 工具执行失败: {result.error}")
            raise ValueError(f"工具执行失败: {result.error if isinstance(result.error, str) else json.dumps(result.error, ensure_ascii=False)}")
