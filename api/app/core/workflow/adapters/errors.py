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


class ExceptionDefineition(BaseModel):
    type: ExceptionType
    detail: str

    node_id: str | None = None
    node_name: str | None = None

    scope: str | None = None
    name: str | None = None


class UnknowModelWarning(ExceptionDefineition):
    type: ExceptionType = ExceptionType.NODE

    def __init__(self, node_id, node_name, model_name):
        super().__init__(
            detail=f"Please specify the model mapping manually for model: {model_name}",
            node_id=node_id,
            node_name=node_name
        )


class UnknowError(ExceptionDefineition):
    type: ExceptionType = ExceptionType.UNKNOWN

    def __init__(self, detail: str, **kwargs):
        super().__init__(detail=detail, **kwargs)


class UnsupportPlatform(ExceptionDefineition):
    type: ExceptionType = ExceptionType.PLATFORM

    def __init__(self, platform: str):
        super().__init__(detail=f"Unsupport platform {platform}")


class UnsupportVariableType(ExceptionDefineition):
    type: ExceptionType = ExceptionType.VARIABLE

    def __init__(self, scope, name, var_type: str, **kwargs):
        super().__init__(scope=scope, name=name, detail=f"Unsupport variable type：[{var_type}]", **kwargs)


class InvalidConfiguration(ExceptionDefineition):
    type: ExceptionType = ExceptionType.CONFIG

    def __init__(self):
        super().__init__(detail="Invalid workflow configuration format")


class UnsupportNodeType(ExceptionDefineition):
    type: ExceptionType = ExceptionType.NODE

    def __init__(self, node_id: str, node_type: str):
        super().__init__(node_id=node_id, detail=f"Unsupport node Type {node_type}")
