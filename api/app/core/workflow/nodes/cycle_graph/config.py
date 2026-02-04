from typing import Any

from pydantic import Field, BaseModel, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.nodes.enums import ComparisonOperator, LogicOperator, ValueInputType


class CycleVariable(BaseNodeConfig):
    name: str = Field(
        ...,
        description="Name of the loop variable"
    )

    type: VariableType = Field(
        ...,
        description="Data type of the loop variable"
    )

    input_type: ValueInputType = Field(
        ...,
        description="Input type of the loop variable"
    )

    value: Any = Field(
        ...,
        description="Initial or current value of the loop variable"
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


class ConditionDetail(BaseModel):
    operator: ComparisonOperator = Field(
        ...,
        description="Operator used to compare the left and right operands"
    )

    left: str = Field(
        ...,
        description="Left-hand operand of the comparison expression"
    )

    right: Any = Field(
        ...,
        description="Right-hand operand of the comparison expression"
    )

    input_type: ValueInputType = Field(
        default=ValueInputType.CONSTANT,
        description="Input type of the loop variable"
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


class ConditionsConfig(BaseModel):
    """Configuration for loop condition evaluation"""

    logical_operator: LogicOperator = Field(
        default=LogicOperator.AND.value,
        description="Logical operator used to combine multiple condition expressions"
    )

    expressions: list[ConditionDetail] = Field(
        ...,
        description="Collection of condition expressions to be evaluated"
    )


class LoopNodeConfig(BaseNodeConfig):
    condition: ConditionsConfig = Field(
        default_factory=list,
        description="Conditional configuration that controls loop execution"
    )

    cycle_vars: list[CycleVariable] = Field(
        default_factory=list,
        description="List of variables used and updated during the loop"
    )

    max_loop: int = Field(
        default=10,
        description="Maximum number of loop iterations"
    )


class IterationNodeConfig(BaseNodeConfig):
    input: str = Field(
        ...,
        description="Input of the loop iteration"
    )

    parallel: bool = Field(
        default=False,
        description="Whether to execute loop iterations in parallel"
    )

    parallel_count: int = Field(
        default=4,
        description="Number of iterations to run in parallel"
    )

    flatten: bool = Field(
        default=False,
        description="Whether to flatten the output list from iterations"
    )

    output: str = Field(
        ...,
        description="Output of the loop iteration"
    )

    output_type: VariableType = Field(
        ...,
        description="Data type of the loop iteration output"
    )


