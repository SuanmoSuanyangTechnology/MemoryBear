from typing import Literal
from pydantic import Field, BaseModel

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.enums import HttpErrorHandle
from app.core.workflow.variable.base_variable import VariableType


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


class CodeRetryConfig(BaseModel):
    """代码节点失败重试配置"""

    enable: bool = Field(
        default=False,
        description="是否启用失败重试"
    )

    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="最大重试次数"
    )

    retry_interval: int = Field(
        default=100,
        ge=10,
        le=60000,
        description="重试间隔（毫秒）"
    )


class CodeErrorHandleConfig(BaseModel):
    """代码节点异常处理配置"""

    method: HttpErrorHandle = Field(
        default=HttpErrorHandle.NONE,
        description="异常处理策略：'none' 抛出异常, 'default' 返回默认值, 'branch' 走异常分支",
    )

    output: str = Field(
        default="",
        description="代码执行异常时返回的默认输出文本（method=default 时生效）",
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

    language: Literal['python3', 'javascript'] = Field(
        ...,
        description="language"
    )

    retry: CodeRetryConfig = Field(
        default_factory=CodeRetryConfig,
        description="失败重试配置"
    )

    error_handle: CodeErrorHandleConfig = Field(
        default_factory=CodeErrorHandleConfig,
        description="异常处理配置"
    )
