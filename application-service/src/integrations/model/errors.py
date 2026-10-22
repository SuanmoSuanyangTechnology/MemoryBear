"""Transport-neutral model service client errors."""

from __future__ import annotations

import logging
from uuid import UUID

from ...errors import BizCode

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

    ``biz_code`` is translated from the service-side code surface (SSE frames give
    ``BizCode`` names, JSON envelopes give numeric values); the semantics are
    **passed through without re-encoding**: callers apply the existing
    ``HTTP_MAPPING`` to decide the outward status.
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
    """Service-side code surface -> host ``BizCode``: names (SSE frames) and numbers (envelopes) share one path.

    The model-domain values of the two enums align item by item (the service-side
    ``src/errors.py`` is a value-for-value port of the host enum), so numeric values
    are accepted directly and names are looked up by name; unknown codes are never
    guessed -- they log a warning and fall back to ``INTERNAL_ERROR``.
    """

    if code is None or isinstance(code, bool):
        return BizCode.INTERNAL_ERROR
    if isinstance(code, int):
        return _BY_VALUE.get(code) or _unknown(code)
    return _BY_NAME.get(str(code).strip().upper()) or _unknown(code)


def _unknown(code: object) -> BizCode:
    logger.warning("model_service_invoke_unknown_code code=%r", code)
    return BizCode.INTERNAL_ERROR
