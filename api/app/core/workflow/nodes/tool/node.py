import logging
import uuid
from typing import Any

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.tool.config import ToolNodeConfig
from app.services.tool_service import ToolService
from app.db import get_db_read

logger = logging.getLogger(__name__)


class ToolNode(BaseNode):
    """工具节点"""
    
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = ToolNodeConfig(**self.config)
    
    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        """执行工具"""
        # 获取租户ID和用户ID
        tenant_id = self.get_variable("sys.tenant_id", state)
        user_id = self.get_variable("sys.user_id", state)
        
        # 如果没有租户ID，尝试从工作流ID获取
        if not tenant_id:
            workflow_id = self.get_variable("sys.workflow_id", state)
            if workflow_id:
                from app.repositories.tool_repository import ToolRepository
                with get_db_read() as db:
                    tenant_id = ToolRepository.get_tenant_id_by_workflow_id(db, workflow_id)
        
        if not tenant_id:
            tenant_id = uuid.UUID("6c2c91b0-3f49-4489-9157-2208aa56a097")
            # logger.error(f"节点 {self.node_id} 缺少租户ID")
            # return {"error": "缺少租户ID"}
        
        # 渲染工具参数
        rendered_parameters = {}
        for param_name, param_template in self.typed_config.tool_parameters.items():
            rendered_value = self._render_template(param_template, state)
            rendered_parameters[param_name] = rendered_value
        
        logger.info(f"节点 {self.node_id} 执行工具 {self.typed_config.tool_id}，参数: {rendered_parameters}")
        print(self.typed_config.tool_id)
        
        # 执行工具
        with get_db_read() as db:
            tool_service = ToolService(db)
            result = await tool_service.execute_tool(
                tool_id=self.typed_config.tool_id,
                parameters=rendered_parameters,
                tenant_id=tenant_id,
                user_id=user_id
            )
        print(result)
        if result.success:
            logger.info(f"节点 {self.node_id} 工具执行成功")
            return {
                "success": True,
                "data": result.data,
                "execution_time": result.execution_time
            }
        else:
            logger.error(f"节点 {self.node_id} 工具执行失败: {result.error}")
            return {
                "success": False,
                "error": result.error,
                "error_code": result.error_code,
                "execution_time": result.execution_time
            }