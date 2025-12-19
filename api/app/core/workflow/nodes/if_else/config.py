"""Condition Configuration"""
from pydantic import Field, BaseModel, field_validator
from enum import StrEnum
from app.core.workflow.nodes.base_config import BaseNodeConfig


class LogicOperator(StrEnum):
    AND = "and"
    OR = "or"


class ComparisonOpeartor(StrEnum):
    EMPTY = "empty"
    NOT_EMPTY = "not_empty"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    START_WITH = "startwith"
    END_WITH = "endwith"
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


class ConditionDetail(BaseModel):
    comparison_operator: ComparisonOpeartor = Field(
        ...,
        description="Comparison operator used to evaluate the condition"
    )

    left: str = Field(
        ...,
        description="Value to compare against"
    )

    right: str = Field(
        ...,
        description="Value to compare with"
    )


class ConditionBranchConfig(BaseModel):
    """Configuration for a conditional branch"""

    logical_operator: LogicOperator = Field(
        default=LogicOperator.AND.value,
        description="Logical operator used to combine multiple condition expressions"
    )

    conditions: list[ConditionDetail] = Field(
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
                            "conditions": [
                                {
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
                                }
                            ]
                        },
                        # CASE1 / ELIF Branch
                        {
                            "logical_operator": "or",
                            "conditions": [
                                {
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
                                }
                            ]
                        }
                        # CASE3 / ELSE Branch
                    ]
                }
            ]
        }
