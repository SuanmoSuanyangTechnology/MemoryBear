import uuid

from pydantic import Field, BaseModel
from enum import StrEnum

from app.core.workflow.nodes.base_config import BaseNodeConfig


class ParamVariableType(StrEnum):
    """
    Enum for variable types that can be extracted as parameters.
    Each member represents a type that can be used in parameter extraction.
    """
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY_STRING = "array[string]"
    ARRAY_NUMBER = "array[number]"
    ARRAY_BOOLEAN = "array[boolean]"
    ARRAY_OBJECT = "array[object]"


class ParamsConfig(BaseModel):
    name: str = Field(
        ...,
        description="Name of the parameter"
    )

    type: ParamVariableType = Field(
        ...,
        description="Type of the parameter"
    )

    desc: str = Field(
        ...,
        description="Description of the parameter"
    )


class ParameterExtractorNodeConfig(BaseNodeConfig):
    model_id: uuid.UUID = Field(
        ...,
        description="Unique identifier for the model"
    )

    text: str = Field(
        ...,
        description="The string to be extracted as a parameter"
    )

    params: list[ParamsConfig] = Field(
        ...,
        description="List of parameters"
    )
