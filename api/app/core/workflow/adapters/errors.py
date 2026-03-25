# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/26 11:29
from enum import StrEnum

from pydantic import BaseModel


class ExceptionType(StrEnum):
    NODE = "node"
    EDGE = "edge"
    VARIABLE = "variable"
    TRIGGER = "trigger"
    EXECUTION = "execution"
    CONFIG = "config"
    PLATFORM = "platform"
    UNKNOWN = "unknown"


class ExceptionDefinition(BaseModel):
    type: ExceptionType
    detail: str

    node_id: str | None = None
    node_name: str | None = None

    scope: str | None = None
    name: str | None = None


class UnknownModelWarning(ExceptionDefinition):
    type: ExceptionType = ExceptionType.NODE

    def __init__(self, node_id, node_name, model_name):
        super().__init__(
            detail=f"Please specify the model mapping manually for model: {model_name}",
            node_id=node_id,
            node_name=node_name
        )


class UnknownError(ExceptionDefinition):
    type: ExceptionType = ExceptionType.UNKNOWN

    def __init__(self, detail: str, **kwargs):
        super().__init__(detail=detail, **kwargs)


class UnsupportedPlatform(ExceptionDefinition):
    type: ExceptionType = ExceptionType.PLATFORM

    def __init__(self, platform: str):
        super().__init__(detail=f"Unsupported platform {platform}")


class UnsupportedVariableType(ExceptionDefinition):
    type: ExceptionType = ExceptionType.VARIABLE

    def __init__(self, scope, name, var_type: str, **kwargs):
        super().__init__(scope=scope, name=name, detail=f"Unsupported variable type: [{var_type}]", **kwargs)


class InvalidConfiguration(ExceptionDefinition):
    type: ExceptionType = ExceptionType.CONFIG

    def __init__(self):
        super().__init__(detail="Invalid workflow configuration format")


class UnsupportedNodeType(ExceptionDefinition):
    type: ExceptionType = ExceptionType.NODE

    def __init__(self, node_id: str, node_type: str):
        super().__init__(node_id=node_id, detail=f"Unsupported node type {node_type}")
