"""Condition Configuration"""
from typing import Any
from pydantic import Field, BaseModel, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.enums import ComparisonOperator, LogicOperator, ValueInputType


class SubVariableConditionItem(BaseModel):
    """A single condition on a file object's field, used inside sub_variable_condition."""
    key: str = Field(..., description="Field name of the file object, e.g. type, size, name")
    operator: ComparisonOperator = Field(..., description="Comparison operator")
    value: Any = Field(default=None, description="Value to compare with")
    var_type: str = Field(default="string", description="Field value type: string or number")


class SubVariableCondition(BaseModel):
    """Sub-conditions applied to each file element in an array[file] variable."""
    logical_operator: LogicOperator = Field(default=LogicOperator.AND)
    conditions: list[SubVariableConditionItem] = Field(default_factory=list)


class ConditionDetail(BaseModel):
    operator: ComparisonOperator = Field(
        ...,
        description="Comparison operator used to evaluate the condition"
    )

    left: str = Field(
        ...,
        description="Variable selector, e.g. {{sys.files}}"
    )

    right: Any = Field(
        default=None,
        description="Value to compare with (unused when sub_variable_condition is set)"
    )

    input_type: ValueInputType = Field(
        default=ValueInputType.CONSTANT,
        description="Value input type for comparison"
    )

    sub_variable_condition: SubVariableCondition | None = Field(
        default=None,
        description="Sub-conditions for array[file] fields. When set, operator must be contains/not_contains."
    )

    @field_validator("input_type", mode="before")
    @classmethod
    def lower_input_type(cls, v):
        if isinstance(v, str):
            try:
                return ValueInputType(v.lower())
            except ValueError:
                raise ValueError(f"Invalid input_type: {v}")
        return v


class ConditionBranchConfig(BaseModel):
    """Configuration for a conditional branch.

    logical_operator controls how all expressions are combined (AND/OR).
    """

    logical_operator: LogicOperator = Field(
        default=LogicOperator.AND,
        description="Logical operator used to combine all conditions"
    )

    expressions: list[ConditionDetail] = Field(
        default_factory=list,
        description="List of conditions within this branch"
    )


class IfElseNodeConfig(BaseNodeConfig):
    cases: list[ConditionBranchConfig] = Field(
        ...,
        description="List of branch conditions or expressions"
    )

    @field_validator("cases")
    @classmethod
    def validate_case_number(cls, v):
        if len(v) < 1:
            raise ValueError("At least one cases are required")
        return v

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "cases": [
                        # CASE1 / IF Branch
                        {
                            "logical_operator": "and",
                            "expressions": [
                                [
                                    {
                                        "left": "node.userinput.message",
                                        "comparison_operator": "eq",
                                        "right": "'123'"
                                    },
                                    {
                                        "left": "node.userinput.test",
                                        "comparison_operator": "eq",
                                        "right": "True"
                                    }
                                ]
                            ]
                        },
                        # CASE1 / ELIF Branch
                        {
                            "logical_operator": "or",
                            "expressions": [
                                [
                                    {
                                        "left": "node.userinput.test",
                                        "comparison_operator": "eq",
                                        "right": "False"
                                    },
                                    {
                                        "left": "node.userinput.message",
                                        "comparison_operator": "contains",
                                        "right": "'123'"
                                    }
                                ]
                            ]
                        }
                        # CASE3 / ELSE Branch
                    ]
                }
            ]
        }
