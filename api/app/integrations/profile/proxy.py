"""Endpoint decorators for optional memory-profile forwarding."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from typing import Any, ParamSpec, TypeVar
from uuid import uuid4

from app.core.error_codes import BizCode
from app.core.response_utils import fail
from app.core.trace import get_trace_id

from .errors import MemoryProfileClientError
from .runtime import get_memory_profile_client, is_memory_profile_enabled

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")
_PROFILE_SOURCE: ContextVar[str] = ContextVar(
    "memory_profile_source", default="manager"
)


async def call_analytics_from_api_key(handler, **kwargs):
    """Invoke a decorated analytics endpoint with API-key source semantics."""

    token = _PROFILE_SOURCE.set("api_key")
    try:
        return await handler(**kwargs)
    finally:
        _PROFILE_SOURCE.reset(token)


def memory_profile_proxy(
    operation: str,
    *,
    query_fields: tuple[str, ...] = (),
    language_field: str | None = None,
    upper_bounds: dict[str, int] | None = None,
):
    """Forward one route to memory-profile when its integration is enabled.

    The wrapped function remains the complete legacy implementation. API-key
    wrappers run their authentication, workspace validation, and merge routing
    before invoking it, so the decorator only replaces the final use-case call.
    """

    bounds = dict(upper_bounds or {})

    def decorate(handler):
        handler_signature = signature(handler)

        @wraps(handler)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R | dict[str, Any]:
            if not is_memory_profile_enabled():
                return await handler(*args, **kwargs)

            arguments = handler_signature.bind_partial(*args, **kwargs).arguments
            current_user = arguments.get("current_user")
            workspace_id = getattr(current_user, "current_workspace_id", None)
            if workspace_id is None:
                return fail(
                    BizCode.INVALID_PARAMETER,
                    "请先切换到一个工作空间",
                    "current_workspace_id is None",
                )

            query = {field: arguments.get(field) for field in query_fields}
            for field, maximum in bounds.items():
                value = query.get(field)
                if value is not None and value > maximum:
                    query[field] = maximum

            trace_id = get_trace_id() or uuid4().hex
            try:
                return await get_memory_profile_client().request(
                    method="GET",
                    operation=operation,
                    workspace_id=str(workspace_id),
                    tenant_id=str(current_user.tenant_id),
                    actor_id=str(current_user.id),
                    source=_PROFILE_SOURCE.get(),
                    trace_id=trace_id,
                    language_type=(
                        arguments.get(language_field) if language_field else None
                    ),
                    query=query,
                )
            except MemoryProfileClientError as exc:
                logger.error(
                    "memory profile proxy failed: operation=%s error=%s trace_id=%s",
                    operation,
                    type(exc).__name__,
                    trace_id,
                )
                return fail(
                    BizCode.SERVICE_UNAVAILABLE,
                    "记忆画像服务暂不可用",
                    "Memory profile service unavailable",
                )

        return wrapped

    return decorate
