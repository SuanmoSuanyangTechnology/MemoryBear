"""Start 节点配置"""

from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig, VariableDefinition
from app.core.workflow.variable.base_variable import VariableType


class StartNodeConfig(BaseNodeConfig):
    """Start 节点配置
    
    Start 节点的作用：
    1. 标记工作流的起点
    2. 定义自定义输入变量（会作为节点输出，通过 start_node_id.variable_name 访问）
    3. 输出系统变量和会话变量
    """
    
    # 自定义输入变量定义
    variables: list[VariableDefinition] = Field(
        default_factory=list,
        description="自定义输入变量列表，这些变量会作为 Start 节点的输出"
    )
    
    # 输出变量定义
    output_variables: list[VariableDefinition] = Field(
        default_factory=lambda: [
            VariableDefinition(
                name="message",
                type=VariableType.STRING,
                description="用户输入的消息"
            ),
            VariableDefinition(
                name="conversation_vars",
                type=VariableType.OBJECT,
                description="会话级变量"
            ),
            VariableDefinition(
                name="execution_id",
                type=VariableType.STRING,
                description="执行 ID"
            ),
            VariableDefinition(
                name="conversation_id",
                type=VariableType.STRING,
                description="会话 ID"
            ),
            VariableDefinition(
                name="workspace_id",
                type=VariableType.STRING,
                description="工作空间 ID"
            ),
            VariableDefinition(
                name="user_id",
                type=VariableType.STRING,
                description="用户 ID"
            )
        ],
        description="输出变量定义（自动生成，通常不需要修改）"
    )
    
    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "description": "工作流开始节点",
                    "variables": []
                },
                {
                    "description": "带自定义变量的开始节点",
                    "variables": [
                        {
                            "name": "language",
                            "type": "string",
                            "required": False,
                            "default": "zh-CN",
                            "description": "语言设置"
                        },
                        {
                            "name": "max_length",
                            "type": "number",
                            "required": False,
                            "default": 1000,
                            "description": "最大长度"
                        }
                    ]
                }
            ]
        }
