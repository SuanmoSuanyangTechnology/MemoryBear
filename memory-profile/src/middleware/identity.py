"""Domain-neutral access to the authenticated caller for Memory modules."""

from __future__ import annotations

from fastapi import Request

from src.common.errors import BizCode, LocalizedProfileError
from src.middleware.auth import MemoryPrincipal

__all__ = ["require_memory_context"]

# Attribute the auth middleware writes on ``request.state`` for every module.
IDENTITY_STATE_KEY = "memory_context"


async def require_memory_context(request: Request) -> MemoryPrincipal:
    """Return the principal ``MemoryAuthMiddleware`` stored on the request.

    Every Memory domain module resolves the caller through this dependency, so
    no domain imports another domain's dependencies module.
    """

    context = getattr(request.state, IDENTITY_STATE_KEY, None)
    if not isinstance(context, MemoryPrincipal):
        raise LocalizedProfileError(
            BizCode.UNAUTHORIZED,
            "common.auth.missing_principal",
            status_code=401,
        )
    return context
