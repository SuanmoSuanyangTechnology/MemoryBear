"""Node config base class

Defines the common fields and data structures shared by all node configs.
"""

from pydantic import BaseModel, Field

from ..variable.base_variable import VariableType

VARIABLE_PATTERN = r"\{\{\s*(.*?)\s*\}\}"


class VariableDefinition(BaseModel):
    """Variable definition

    Defines workflow or node input/output variables.
    This is a generic data structure that can be used in multiple places.
    """

    name: str = Field(
        ...,
        description="变量名称"
    )

    type: VariableType = Field(
        default=VariableType.STRING,
        description="变量类型"
    )

    ui_type: str | None = Field(
        default=None,
        description=(
            "UI展示类型，区分同一底层类型的不同前端形态。"
            "可选值: text-input, paragraph, select, file-upload, "
            "file-list-upload, json-editor"
        )
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

    options: list[str] | None = Field(
        default=None,
        description="下拉选项的候选值列表，仅 ui_type=select 时有效"
    )

    allowed_file_types: list[str] | None = Field(
        default=None,
        description="允许的文件类型列表(image/document/audio/video)，仅 file/file-list-upload 时有效"
    )

    max_file_count: int | None = Field(
        default=5,
        description="文件列表最大文件数量，仅 file-list-upload 时有效"
    )

    max_file_size_mb: float | None = Field(
        default=50.0,
        description="单个文件最大大小(MB)，仅 file/file-list-upload 时有效"
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
    """Node config base class

    All node configs should inherit from this base class.

    Common fields:
    - name: Node name (display name)
    - description: Node description
    - tags: Node tags (used for categorization and search)
    """

    # name: str | None = Field(
    #     default=None,
    #     description="Node name (display name); node ID is used when unset"
    # )
    #
    # description: str | None = Field(
    #     default=None,
    #     description="Node description, explaining what the node does"
    # )
    #
    # tags: list[str] = Field(
    #     default_factory=list,
    #     description="Node tags, used for categorization and search"
    # )

    class Config:
        """Pydantic config"""
        # Allow extra fields (backward compatible)
        extra = "allow"
