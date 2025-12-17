"""End 节点配置"""

from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig, VariableDefinition, VariableType


class EndNodeConfig(BaseNodeConfig):
    """End 节点配置
    
    End 节点负责输出工作流的最终结果。
    """
    
    output: str = Field(
        default="工作流已完成",
        description="输出模板，支持引用前置节点的输出，如：{{ llm_qa.output }}"
    )
    
    # 输出变量定义
    output_variables: list[VariableDefinition] = Field(
        default_factory=lambda: [
            VariableDefinition(
                name="output",
                type=VariableType.STRING,
                description="工作流的最终输出"
            )
        ],
        description="输出变量定义（自动生成，通常不需要修改）"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "output": "{{ llm_qa.output }}",
                "description": "输出 LLM 的回答"
            }
        }
