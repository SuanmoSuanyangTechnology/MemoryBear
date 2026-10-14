"""Start node config."""

from pydantic import Field

from ...nodes.base_config import BaseNodeConfig, VariableDefinition


class StartNodeConfig(BaseNodeConfig):
    """Start node config

    The Start node:
    1. Marks the entry point of the workflow
    2. Defines custom input variables (node outputs, accessed via start_node_id.variable_name)
    3. Outputs system variables and conversation variables
    """
    
    # Custom input variable definitions
    variables: list[VariableDefinition] = Field(
        default_factory=list,
        description="自定义输入变量列表，这些变量会作为 Start 节点的输出"
    )
    
    # # Output variable definitions
    # output_variables: list[VariableDefinition] = Field(
    #     default_factory=lambda: [
    #         VariableDefinition(
    #             name="message",
    #             type=VariableType.STRING,
    #             description="message entered by the user"
    #         ),
    #         VariableDefinition(
    #             name="conversation_vars",
    #             type=VariableType.OBJECT,
    #             description="conversation-level variables"
    #         ),
    #         VariableDefinition(
    #             name="execution_id",
    #             type=VariableType.STRING,
    #             description="execution ID"
    #         ),
    #         VariableDefinition(
    #             name="conversation_id",
    #             type=VariableType.STRING,
    #             description="conversation ID"
    #         ),
    #         VariableDefinition(
    #             name="workspace_id",
    #             type=VariableType.STRING,
    #             description="workspace ID"
    #         ),
    #         VariableDefinition(
    #             name="user_id",
    #             type=VariableType.STRING,
    #             description="user ID"
    #         )
    #     ],
    #     description="Output variable definitions (auto-generated, usually no changes needed)"
    # )
    
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
