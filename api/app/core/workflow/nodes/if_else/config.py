"""Condition Configuration"""
from typing import Any
from pydantic import Field, BaseModel, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.enums import ComparisonOperator, LogicOperator, ValueInputType


class ConditionDetail(BaseModel):
    operator: ComparisonOperator = Field(
        ...,
        description="Comparison operator used to evaluate the condition"
    )

    left: str = Field(
        ...,
        description="Value to compare against"
    )

    right: Any = Field(
        ...,
        description="Value to compare with"
    )

    input_type: ValueInputType = Field(
        default=ValueInputType.CONSTANT,
        description="Value input type for comparison"
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
    """Configuration for a conditional branch"""

    logical_operator: LogicOperator = Field(
        default=LogicOperator.AND,
        description="Logical operator used to combine multiple condition expressions"
    )

    expressions: list[ConditionDetail] = Field(
        ...,
        description="List of condition expressions within this branch"
    )


class IfElseNodeConfig(BaseNodeConfig):
    cases: list[ConditionBranchConfig] = Field(
        ...,
        description="List of branch conditions or expressions"
    )

    @field_validator("cases")
    @classmethod
    def validate_case_number(cls, v, info):
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
