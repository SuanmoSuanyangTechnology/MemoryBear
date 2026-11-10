"""Transport-neutral model service client errors."""

from __future__ import annotations

import logging
from uuid import UUID

from error_core.codes import coerce_code

from ...errors import BizCode

logger = logging.getLogger(__name__)


class ModelServiceClientError(Exception):
    """Base class for model service integration failures."""


class ModelServiceUnavailableError(ModelServiceClientError):
    """The model service could not be reached."""


class ModelInvokeCancelledError(ModelServiceClientError):
    """The invoke was cancelled: the caller went away or the service aborted the RPC.

    Not a failure. Boundaries must follow the 499 client-disconnect semantics:
    log only, never emit an error frame or retry.
    """


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
    ``HTTP_MAPPING`` to decide the outward status. ``user_msg`` carries the
    service-side localized copy verbatim, while ``detail`` carries the diagnostic
    surface (provider/status plus the upstream service suffix).
    """

    def __init__(
        self,
        *,
        biz_code: int | BizCode,
        message: str,
        user_msg: str | None = None,
        detail: str | None = None,
        attempts: int | None = None,
        channel_id: UUID | None = None,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        http_status: int | None = None,
    ) -> None:
        self.biz_code = biz_code
        self.user_msg = user_msg
        self.detail = detail if detail is not None else message
        # ``message`` is a compatibility alias of the diagnostic surface, matching
        # ``RemoteInvokeFailedError`` in the model package.
        self.message = self.detail
        self.attempts = attempts
        self.channel_id = channel_id
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.http_status = http_status
        super().__init__(message)


def biz_code_from_remote(value: int | str | None) -> int:
    """Service-side code surface -> numeric host code, preserving unknown numbers.

    Known enum names and digit strings are coerced through ``error_core``; an
    unknown numeric code passes through unchanged, while an unknown name falls
    back to ``UPSTREAM_UNKNOWN`` after logging a warning.
    """

    numeric = coerce_code(value)
    if numeric is not None:
        return numeric
    logger.warning("unknown remote biz code: %r", value)
    return int(BizCode.UPSTREAM_UNKNOWN)
