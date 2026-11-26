"""Shared-secret guard for the identity-service callback endpoints.

During the coexistence period the identity service calls back into the monolith
for workspace-model reads/writes whose data and side effects stay host-owned
(active reembed job id, the single-transaction workspace-model write). Those
paths authenticate with a dedicated pre-shared secret instead of a platform
credential: no user token crosses the internal hop, and the secret cannot be
replayed against the public face. Both ends configure the same value through
IDENTITY_CALLBACK_SECRET; guard failures fail closed and never log or echo the
presented value.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_api_logger

logger = get_api_logger()

CALLBACK_SECRET_ENV = "IDENTITY_CALLBACK_SECRET"
CALLBACK_SECRET_HEADER = "X-Callback-Secret"


def require_identity_callback_secret(
    x_callback_secret: Optional[str] = Header(None, alias=CALLBACK_SECRET_HEADER),
) -> None:
    """Validate the callback shared secret; missing configuration fails closed.

    The value is read per request (not at import time) so deployments and
    tests can inject it after module import. Comparison is constant-time over
    bytes: header values are not guaranteed to be ASCII.
    """

    expected = os.getenv(CALLBACK_SECRET_ENV, "")
    if not expected:
        logger.error(
            "%s is not configured; rejecting internal callback request",
            CALLBACK_SECRET_ENV,
        )
        raise BusinessException("内部回调凭据未配置", BizCode.UNAUTHORIZED)

    provided = x_callback_secret or ""
    if not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        logger.warning("Rejected internal callback request with an invalid secret")
        raise BusinessException("内部回调凭据无效", BizCode.UNAUTHORIZED)
