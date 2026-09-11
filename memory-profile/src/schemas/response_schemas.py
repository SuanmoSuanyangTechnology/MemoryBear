import time
from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    """统一响应包体。

    ``error_code`` 必须声明在此：FastAPI 的 ``response_model`` 会**过滤掉未声明的
    字段**，不声明的话 ``fail()`` 里带的 ``error_code`` 会在序列化时被丢掉——
    而客户端契约正是"按 ``error_code`` 字符串分支"（见 docs/memory_profile_biz_codes.md）。
    （i18n 异常走的 exception handler 不经 response_model，所以 C 类响应不受影响。）
    """

    code: int = Field(default=0, description="业务码，0=成功，非 0 见 docs/memory_profile_biz_codes.md")
    error_code: str = Field(default="", description="稳定错误标识（= BizCode 名，如 END_USER_NOT_FOUND），按它分支")
    msg: str = Field(default="OK", description="已按请求语言翻译的提示，可直接展示")
    data: Any | None = Field(default=None)
    error: str = Field(default="", description="失败时的错误信息，成功为空字符串")
    time: int = Field(default_factory=lambda: int(time.time()))
