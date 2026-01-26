from typing import Literal
from pydantic import Field, BaseModel

from app.core.workflow.nodes.base_config import BaseNodeConfig, VariableType


class InputVariable(BaseModel):
    name: str = Field(
        ...,
        description="variable name"
    )

    variable: str = Field(
        ...,
        description="variable selector"
    )


class OutputVariable(BaseModel):
    name: str = Field(
        ...,
        description="variable name"
    )

    type: VariableType = Field(
        ...,
        description="variable selector"
    )


class CodeNodeConfig(BaseNodeConfig):
    input_variables: list[InputVariable] = Field(
        default_factory=list,
        description="input variables"
    )

    output_variables: list[OutputVariable] = Field(
        default_factory=list,
        description="output variables"
    )

    code: str = Field(
        default="",
        description="code content"
    )

    language: Literal['python3', 'nodejs'] = Field(
        ...,
        description="language"
    )
