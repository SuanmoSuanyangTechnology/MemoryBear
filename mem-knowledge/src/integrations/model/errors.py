"""km 侧 invoke 错误词表：包内 ``RemoteInvokeError`` 的唯一翻译落点。

与宿主 ``ModelServiceClientError`` 族同构，但**不带 BizCode**：km 的码面是字符串
``KB_*`` 词表（``src.errors``），映射发生在业务层 error_mapping，本层只原样携带服务侧
错误码（``remote_code``：SSE 帧给名字、JSON 信封给数值），不猜语义。
"""

from __future__ import annotations

import logging
from uuid import UUID

from redbear_model.errors import (
    RemoteInvokeError,
    RemoteInvokeFailedError,
    RemoteInvokeIdleTimeoutError,
    RemoteInvokeProtocolError,
)

logger = logging.getLogger(__name__)


class ModelInvokeClientError(Exception):
    """Base class for model service invoke failures."""


class ModelInvokeUnavailableError(ModelInvokeClientError):
    """The model service could not be reached."""


class ModelInvokeTimeoutError(ModelInvokeClientError):
    """The model service produced no frame before the configured idle timeout."""


class ModelInvokeConfigurationError(ModelInvokeClientError):
    """The invoke integration configuration is invalid."""


class ModelInvokeProtocolError(ModelInvokeClientError):
    """The model service violated the response contract (malformed or truncated stream)."""


class ModelInvokeFailedError(ModelInvokeClientError):
    """The model service ran the invoke and reported a terminal failure.

    ``remote_code`` 为服务侧原样码（SSE 帧 = ``BizCode`` 名、JSON 信封 = 数值）；
    业务层按需翻译回 ``KB_*`` 词表（``error_mapping``），本层不重编码。
    """

    def __init__(
        self,
        *,
        remote_code: str | int | None,
        message: str,
        attempts: int | None = None,
        channel_id: UUID | None = None,
        retryable: bool = False,
        http_status: int | None = None,
    ):
        self.remote_code = remote_code
        self.message = message
        self.attempts = attempts
        self.channel_id = channel_id
        self.retryable = retryable
        self.http_status = http_status
        super().__init__(f"Model invoke failed (remote_code={remote_code!r}): {message}")

    @property
    def status_code(self) -> int | None:
        """包内 ``is_provider_rate_limit_error`` 按此属性识别 429（无则整类限流判定失效）。"""

        return self.http_status


def to_invoke_error(exc: RemoteInvokeError) -> ModelInvokeClientError:
    """包内错误 → km 错误语汇（唯一翻译点；码面透传不重编码）。"""

    if isinstance(exc, RemoteInvokeIdleTimeoutError):
        error: ModelInvokeClientError = ModelInvokeTimeoutError(
            f"Model invoke produced no frame within {exc.idle_seconds:g}s"
        )
    elif isinstance(exc, RemoteInvokeProtocolError):
        error = ModelInvokeProtocolError(str(exc))
    elif isinstance(exc, RemoteInvokeFailedError):
        error = ModelInvokeFailedError(
            remote_code=exc.code,
            message=exc.message,
            attempts=exc.attempts,
            channel_id=exc.channel_id,
            retryable=exc.retryable,
            http_status=exc.http_status,
        )
    else:
        error = ModelInvokeUnavailableError("Model service is unavailable")
    logger.warning(
        "model_invoke_failed error=%s remote=%s detail=%s",
        type(error).__name__,
        type(exc).__name__,
        exc,
    )
    return error


__all__ = [
    "ModelInvokeClientError",
    "ModelInvokeConfigurationError",
    "ModelInvokeFailedError",
    "ModelInvokeProtocolError",
    "ModelInvokeTimeoutError",
    "ModelInvokeUnavailableError",
    "to_invoke_error",
]
