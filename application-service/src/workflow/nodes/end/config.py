"""End node config."""
from pydantic import Field

from ...nodes.base_config import BaseNodeConfig


class EndNodeConfig(BaseNodeConfig):
    """End node config

    The End node is responsible for outputting the final result of the workflow.
    """
    
    output: str = Field(
        default="工作流已完成",
        description="输出模板，支持引用前置节点的输出，如：{{ llm_qa.output }}"
    )
    
    # # Output variable definitions
    # output_variables: list[VariableDefinition] = Field(
    #     default_factory=lambda: [
    #         VariableDefinition(
    #             name="output",
    #             type=VariableType.STRING,
    #             description="The final output of the workflow"
    #         )
    #     ],
    #     description="Output variable definitions (auto-generated, usually no changes needed)"
    # )
    
    class Config:
        json_schema_extra = {
            "example": {
                "output": "{{ llm_qa.output }}",
                "description": "输出 LLM 的回答"
            }
        }
