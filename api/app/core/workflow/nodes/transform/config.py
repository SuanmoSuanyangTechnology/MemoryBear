"""Transform 节点配置"""

from typing import Literal

from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig, VariableDefinition, VariableType


class TransformNodeConfig(BaseNodeConfig):
    """Transform 节点配置
    
    用于数据转换和处理。
    """
    
    transform_type: Literal["template", "code", "json"] = Field(
        default="template",
        description="转换类型：template(模板), code(代码), json(JSON处理)"
    )
    
    # 模板模式
    template: str | None = Field(
        default=None,
        description="转换模板，支持变量引用"
    )
    
    # 代码模式
    code: str | None = Field(
        default=None,
        description="Python 代码，用于数据转换"
    )
    
    # JSON 模式
    json_path: str | None = Field(
        default=None,
        description="JSON 路径表达式"
    )
    
    # 输入变量
    inputs: dict[str, str] | None = Field(
        default=None,
        description="输入变量映射，key 为变量名，value 为变量选择器"
    )
    
    # 输出变量
    output_key: str = Field(
        default="result",
        description="输出变量的键名"
    )
    
    # 输出变量定义
    output_variables: list[VariableDefinition] = Field(
        default_factory=lambda: [
            VariableDefinition(
                name="result",
                type=VariableType.STRING,
                description="转换后的结果"
            )
        ],
        description="输出变量定义（根据 output_key 动态生成）"
    )
    
    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "transform_type": "template",
                    "template": "用户问题：{{ sys.message }}\n回答：{{ llm_qa.output }}",
                    "output_key": "formatted_result"
                },
                {
                    "transform_type": "code",
                    "code": "result = input_text.upper()",
                    "inputs": {
                        "input_text": "{{ sys.message }}"
                    },
                    "output_key": "uppercase_text"
                }
            ]
        }
