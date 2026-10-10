"""Transport-neutral model service client errors."""

from __future__ import annotations

import logging
from uuid import UUID

from app.core.error_codes import BizCode

logger = logging.getLogger(__name__)


class ModelServiceClientError(Exception):
    """Base class for model service integration failures."""


class ModelServiceUnavailableError(ModelServiceClientError):
    """The model service could not be reached."""


class ModelServiceTimeoutError(ModelServiceClientError):
    """The model service did not respond before the configured timeout."""


class ModelServiceConfigurationError(ModelServiceClientError):
    """The model service integration configuration is invalid."""


class ModelServiceProtocolError(ModelServiceClientError):
    """The model service violated the response contract (malformed or truncated stream)."""


class ModelInvokeFailedError(ModelServiceClientError):
    """The model service ran the invoke and reported a terminal failure.

    ``biz_code`` 由服务侧码面翻译而来（SSE 帧给 ``BizCode`` 名、JSON 信封给数值），
    语义**透传不重编码**（设计 §2.4）：调用方按既有 ``HTTP_MAPPING`` 决定对外状态。
    """

    def __init__(
        self,
        *,
        biz_code: BizCode,
        message: str,
        attempts: int | None = None,
        channel_id: UUID | None = None,
        retryable: bool = False,
        http_status: int | None = None,
    ):
        self.biz_code = biz_code
        self.message = message
        self.attempts = attempts
        self.channel_id = channel_id
        self.retryable = retryable
        self.http_status = http_status
        super().__init__(f"Model invoke failed ({biz_code.name}): {message}")


_BY_NAME = {code.name: code for code in BizCode}
_BY_VALUE = {int(code): code for code in BizCode}


def biz_code_from_remote(code: str | int | None) -> BizCode:
    """服务侧码面 → 宿主 ``BizCode``：名字（SSE 帧）与数值（信封）双形态同径。

    两枚举的模型域取值逐项对齐（服务侧 ``src/errors.py`` 是宿主的按位移植），故数值
    直接互认、名字按名查表；未知码不猜语义——记警告后归 ``INTERNAL_ERROR``。
    """

    if code is None or isinstance(code, bool):
        return BizCode.INTERNAL_ERROR
    if isinstance(code, int):
        return _BY_VALUE.get(code) or _unknown(code)
    return _BY_NAME.get(str(code).strip().upper()) or _unknown(code)


def _unknown(code: object) -> BizCode:
    logger.warning("model_service_invoke_unknown_code code=%r", code)
    return BizCode.INTERNAL_ERROR
