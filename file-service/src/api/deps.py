from __future__ import annotations

from fastapi import Request

from ..auth import Principal
from ..errors import BizCode, BusinessException


def require_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise BusinessException("authentication required", BizCode.UNAUTHORIZED, 401)
    return principal


def optional_principal(request: Request) -> Principal | None:
    return getattr(request.state, "principal", None)
