from pydantic import Field, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig


class VariableAggregatorNodeConfig(BaseNodeConfig):
    group: bool = Field(
        ...,
        description="输出变量是否需要分组",
    )

    group_names: list[str] = Field(
        default_factory=lambda: ["output"],
        description="各个分组的名称"
    )

    group_variables: list[str] | list[list[str]] = Field(
        ...,
        description="需要被聚合的变量"
    )

    @field_validator("group_names", mode="before")
    @classmethod
    def group_names_validator(cls, v, info):
        group_status = info.data.get("group")
        if not group_status or not v:
            return ["output"]
        return v

    @field_validator("group_variables")
    @classmethod
    def group_variables_validator(cls, v, info):
        group_status = info.data.get("group")
        group_names = info.data.get("group_names")
        if not isinstance(v, list):
            raise ValueError("group_variables must be a list")

        if not group_status:
            for variable in v:
                if not isinstance(variable, str):
                    raise ValueError("When group=False, group_variables must be a list of strings")
        else:
            if len(group_names) != len(v):
                raise ValueError("group_names and group_variables length mismatch")
            for group in v:
                if not isinstance(group, list):
                    raise ValueError("When group=True, each element of group_variables must be a list")
                for variable in group:
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
