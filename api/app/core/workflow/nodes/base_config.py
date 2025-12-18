"""节点配置基类

定义所有节点配置的通用字段和数据结构。
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class VariableType(StrEnum):
    """变量类型枚举"""
    
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class VariableDefinition(BaseModel):
    """变量定义
    
    定义工作流或节点的输入/输出变量。
    这是一个通用的数据结构，可以在多个地方使用。
    """
    
    name: str = Field(
        ...,
        description="变量名称"
    )
    
    type: VariableType = Field(
        default=VariableType.STRING,
        description="变量类型"
    )
    
    required: bool = Field(
        default=False,
        description="是否必需"
    )
    
    default: str | int | float | bool | list | dict | None = Field(
        default=None,
        description="默认值"
    )
    
    description: str | None = Field(
        default=None,
        description="变量描述"
    )
    
    max_length: int = Field(
        default=200,
        description="只对字符串类型生效"
    )
    
    class Config:
        json_schema_extra = {
            "examples": [
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
                },
                {
                    "name": "enable_search",
                    "type": "boolean",
                    "required": True,
                    "description": "是否启用搜索"
                }
            ]
        }


class BaseNodeConfig(BaseModel):
    """节点配置基类
    
    所有节点配置都应该继承此基类。
    
    通用字段：
    - name: 节点名称（显示名称）
    - description: 节点描述
    - tags: 节点标签（用于分类和搜索）
    """
    
    name: str | None = Field(
        default=None,
        description="节点名称（显示名称），如果不设置则使用节点 ID"
    )
    
    description: str | None = Field(
        default=None,
        description="节点描述，说明节点的作用"
    )
    
    tags: list[str] = Field(
        default_factory=list,
        description="节点标签，用于分类和搜索"
    )
    
    class Config:
        """Pydantic 配置"""
        # 允许额外字段（向后兼容）
        extra = "allow"
