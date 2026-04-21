from typing import Any
from pydantic import Field
from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.variable.base_variable import VariableType


class OutputItemConfig(BaseNodeConfig):
    name: str
    type: VariableType = VariableType.STRING
    value: Any = ""


class OutputNodeConfig(BaseNodeConfig):
    outputs: list[OutputItemConfig] = Field(default_factory=list)
