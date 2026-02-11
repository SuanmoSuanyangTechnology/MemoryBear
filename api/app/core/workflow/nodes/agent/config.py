"""Agent 节点配置"""

from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig, VariableDefinition
from app.core.workflow.variable.base_variable import VariableType


class AgentNodeConfig(BaseNodeConfig):
    """Agent 节点配置
    
    调用已配置的 Agent 执行任务。
    """
    
    agent_id: str = Field(
        ...,
        description="Agent 配置 ID"
    )
    
    message: str = Field(
        default="{{ sys.message }}",
        description="发送给 Agent 的消息，支持模板变量"
    )
    
    conversation_id: str | None = Field(
        default=None,
        description="会话 ID，用于多轮对话"
    )
    
    variables: dict[str, str] | None = Field(
        default=None,
        description="传递给 Agent 的变量"
    )
    
    timeout: int = Field(
        default=300,
        ge=1,
        le=3600,
        description="超时时间（秒）"
    )
    
    # 输出变量定义
    output_variables: list[VariableDefinition] = Field(
        default_factory=lambda: [
            VariableDefinition(
                name="output",
                type=VariableType.STRING,
                description="Agent 的回复内容"
            ),
            VariableDefinition(
                name="conversation_id",
                type=VariableType.STRING,
                description="会话 ID"
            ),
            VariableDefinition(
                name="token_usage",
                type=VariableType.OBJECT,
                description="Token 使用情况"
            )
        ],
        description="输出变量定义（自动生成，通常不需要修改）"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "agent_id": "uuid-here",
                "message": "{{ sys.message }}",
                "timeout": 300,
                "description": "调用客服 Agent"
            }
        }
