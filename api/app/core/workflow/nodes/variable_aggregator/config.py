from pydantic import Field, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.variable.base_variable import VariableType


class VariableAggregatorNodeConfig(BaseNodeConfig):
    group: bool = Field(
        ...,
        description="输出变量是否需要分组",
    )

    group_variables: list[str] | dict[str, list[str]] = Field(
        ...,
        description="需要被聚合的变量"
    )

    group_type: dict[str, VariableType] = Field(
        default=None,
        description="每个分组的变量类型"
    )

    @field_validator("group_variables")
    @classmethod
    def group_variables_validator(cls, v, info):
        group_status = info.data.get("group")

        if not group_status:
            for variable in v:
                if not isinstance(variable, str):
                    raise ValueError("When group=False, group_variables must be a list of strings")
        else:
            if not isinstance(v, dict):
                raise ValueError("When group=True, group_variables must be a dict")
            for group_name, group_values in v.items():
                if not isinstance(group_name, str):
                    raise ValueError("When group=True, each element of group_variables must be a list")
                for variable in group_values:
                    if not isinstance(variable, str):
                        raise ValueError("Each element inside group_variables lists must be a string")
        return v

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "group": True,
                    "group_names": [
                        "user_message",
                        "conv_var"
                    ],
                    "group_variables": [
                        [
                            "{{start.test_none}}",
                            "{{start.test}}"
                        ],
                        [
                            "{{conv.test_1}}",
                            "{{conv.test_2}}"
                        ]
                    ]
                }
            ]
        }
