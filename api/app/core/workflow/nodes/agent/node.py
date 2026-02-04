"""
Agent 节点实现

调用已发布的 Agent 应用。
# TODO
"""

import logging
from typing import Any
from langchain_core.messages import AIMessage

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool
from app.services.draft_run_service import DraftRunService
from app.models import AppRelease
from app.db import get_db

logger = logging.getLogger(__name__)


class AgentNode(BaseNode):
    """Agent 节点
    
    支持流式和非流式输出。
    
    配置示例:
    {
        "type": "agent",
        "config": {
            "agent_id": "uuid",  # Agent 的 release_id
            "message": "{{var.user_input}}"
        }
    }
    """

    def _output_types(self) -> dict[str, VariableType]:
        return {"output": VariableType.STRING}

    def _prepare_agent(self, variable_pool: VariablePool) -> tuple[DraftRunService, AppRelease, str]:
        """准备 Agent（公共逻辑）
        
        Args:
            variable_pool: 变量池
        
        Returns:
            (draft_service, release, message): 服务实例、发布配置、消息
        """
        # 1. 渲染消息
        message_template = self.config.get("message", "")
        message = self._render_template(message_template, variable_pool)
        
        # 2. 获取 Agent 配置
        agent_id = self.config.get("agent_id")
        if not agent_id:
            raise ValueError(f"节点 {self.node_id} 缺少 agent_id 配置")
        
        db = next(get_db())
        release = db.query(AppRelease).filter(
            AppRelease.id == agent_id
        ).first()
        
        if not release:
            raise ValueError(f"Agent 不存在: {agent_id}")
        
        draft_service = DraftRunService(db)
        
        return draft_service, release, message
    
    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """非流式执行
        
        Args:
            state: 工作流状态
            variable_pool: 变量池
        
        Returns:
            状态更新字典
        """
        draft_service, release, message = self._prepare_agent(variable_pool)
        
        logger.info(f"节点 {self.node_id} 开始执行 Agent 调用（非流式）")
        
        # 执行 Agent（非流式）
        result = await draft_service.run(
            agent_config=release.config,
            model_config=None,
            message=message,
            workspace_id=variable_pool.get_value("sys.workspace_id"),
            user_id=state.get("user_id"),
            variables=variable_pool.get_all_conversation_vars()
        )
        
        response = result.get("response", "")
        
        logger.info(f"节点 {self.node_id} Agent 调用完成，输出长度: {len(response)}")
        
        return {
            "messages": [AIMessage(content=response)],
            "node_outputs": {
                self.node_id: {
                    "output": response,
                    "status": "completed",
                    "meta_data": result.get("meta_data", {})
                }
            }
        }
    
    async def execute_stream(self, state: WorkflowState, variable_pool: VariablePool):
        """流式执行
        
        Args:
            state: 工作流状态
            variable_pool: 变量池
        
        Yields:
            流式事件字典
        """
        draft_service, release, message = self._prepare_agent(variable_pool)
        
        logger.info(f"节点 {self.node_id} 开始执行 Agent 调用（流式）")
        
        # 累积完整响应
        full_response = ""
        
        # 执行 Agent（流式）
        async for chunk in draft_service.run_stream(
            agent_config=release.config,
            model_config=None,
            message=message,
            workspace_id=variable_pool.get_value("sys.workspace_id"),
            user_id=state.get("user_id"),
            variables=variable_pool.get_all_conversation_vars()
        ):
            # 提取内容
            content = chunk.get("content", "")
            full_response += content
            
            # 流式返回每个 chunk
            yield {
                "type": "chunk",
                "node_id": self.node_id,
                "content": content,
                "full_content": full_response,
                "meta_data": chunk.get("meta_data", {})
            }
        
        logger.info(f"节点 {self.node_id} Agent 调用完成，输出长度: {len(full_response)}")
        
        # 最后返回完整结果
        yield {
            "type": "complete",
            "messages": [AIMessage(content=full_response)],
            "node_outputs": {
                self.node_id: {
                    "output": full_response,
                    "status": "completed"
                }
            }
        }
