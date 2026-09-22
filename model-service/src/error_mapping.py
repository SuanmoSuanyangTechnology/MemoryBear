"""Map runtime channel failures onto the business error contract."""

from __future__ import annotations

from .errors import BizCode, BusinessException


def channel_error_to_business(exc: BaseException) -> BusinessException:
    """渠道链耗尽/空链 → NO_AVAILABLE_CHANNEL 业务错误（HTTP 边界映射；文案不含凭据）。"""

    return BusinessException(str(exc), BizCode.NO_AVAILABLE_CHANNEL)


__all__ = ["channel_error_to_business"]
