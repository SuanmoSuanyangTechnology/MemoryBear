"""Endpoint decorators for optional memory-profile forwarding."""

from __future__ import annotations

import logging
from functools import wraps
from inspect import signature
from typing import Any, ParamSpec, TypeVar
from uuid import uuid4

from fastapi import Request, Response

from app.core.error_codes import BizCode
from app.core.response_utils import fail
from app.core.trace import get_trace_id

from .contracts import MemoryProfileAuthSource, MemoryProfileRawResponse
from .errors import MemoryProfileClientError, MemoryProfileConfigurationError
from .runtime import get_memory_profile_client, is_memory_profile_enabled

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")
_API_KEY_FACE_PREFIX = "/v1/"


def _caller_source(path: str) -> MemoryProfileAuthSource:
    """Name the source the caller arrived from, off the path it reached us through.

    ``/v1`` is the API-key face and everything else is an internal call (a user
    JWT terminated upstream) — the same split the Gateway uses when it labels the
    request. Deriving it from the path the service is about to receive keeps the
    label and the forwarded path from ever drifting apart.
    """

    if path.startswith(_API_KEY_FACE_PREFIX):
        return MemoryProfileAuthSource.APIKEY
    return MemoryProfileAuthSource.INTERNAL


def _caller_actor_id(current_user: Any, source: MemoryProfileAuthSource) -> str:
    """The id memory-profile identifies this call by, chosen per face.

    The API-key face is identified by the key that authenticated rather than by
    the user behind it: the Gateway injects ``x-api-key-id`` on that face and no
    user id at all, so forwarding the creator here would make one ``ex_api``
    request mean two different things depending on which hop terminated the
    credential. The key's workspace is already what this face forwards (the API's
    own ownership check uses ``api_key_auth.workspace_id`` too), so the actor is
    the last piece of the identity that still came from the creator.

    The key comes from the ``require_api_key_self_db`` context, the same channel
    the handler's own ``api_key_auth`` parameter is fed from; its absence is a
    wiring error, and the getter says so.
    """

    if source == MemoryProfileAuthSource.APIKEY:
        # Deferred: app.core.api_key_auth pulls in the service/repository layer,
        # and this module is imported from the controllers that layer feeds.
        from app.core.api_key_auth import get_current_api_key_auth

        return str(get_current_api_key_auth().api_key_id)
    return str(current_user.id)


def memory_profile_proxy(
    *,
    query_fields: tuple[str, ...] = (),
    language_field: str | None = None,
    upper_bounds: dict[str, int] | None = None,
    source: MemoryProfileAuthSource | None = None,
):
    """Forward one route to memory-profile when its integration is enabled.

    The wrapped function remains the complete legacy implementation. The API-key
    routes run their authentication, workspace validation, and merge routing
    before calling it, so the decorator only replaces the final use-case call.
    Forwarding is path-driven: the handler declares ``request: Request`` and its
    caller path is forwarded, so adding an endpoint needs no entry here, and the
    source is read off that same path. Pass ``source`` to pin it for a caller that
    has no such path (a task or script invoking the handler directly); an explicit
    ``source`` wins over the path.
    """

    bounds = dict(upper_bounds or {})

    def decorate(handler):
        handler_signature = signature(handler)

        @wraps(handler)
        async def wrapped(
            *args: P.args, **kwargs: P.kwargs
        ) -> R | dict[str, Any] | Response:
            if not is_memory_profile_enabled():
                return await handler(*args, **kwargs)

            # FastAPI and the API-key callers invoke handlers with keyword
            # arguments; binding the full signature costs ~2µs per request.
            if "request" in kwargs and "current_user" in kwargs:
                arguments = kwargs
            else:
                arguments = handler_signature.bind_partial(*args, **kwargs).arguments
            request = arguments.get("request")
            if not isinstance(request, Request):
                raise MemoryProfileConfigurationError(
                    "memory_profile_proxy requires the handler to declare "
                    "request: Request"
                )
            current_user = arguments.get("current_user")
            workspace_id = getattr(current_user, "current_workspace_id", None)
            if workspace_id is None:
                return fail(
                    BizCode.INVALID_PARAMETER,
                    "请先切换到一个工作空间",
                    "current_workspace_id is None",
                )

            # The query is rebuilt from the declared fields rather than forwarded
            # raw: API-key callers reach the handler through merge routing, so the
            # caller's own end_user_id may still name a merged-away end user.
            query = {field: arguments.get(field) for field in query_fields}
            for field, maximum in bounds.items():
                value = query.get(field)
                if value is not None and value > maximum:
                    query[field] = maximum

            path = request.scope["path"]
            # Language is presentation-only, so every proxied route forwards it
            # even when the handler does not declare the header. An explicit
            # ``language_field`` still wins for callers that carry it elsewhere.
            language_type = (
                arguments.get(language_field) if language_field else None
            )
            if language_type is None:
                language_type = request.headers.get("X-Language-Type")
            if language_type is None:
                language_type = request.headers.get("Accept-Language")
            trace_id = get_trace_id() or uuid4().hex
            caller_source = _caller_source(path) if source is None else source
            try:
                result = await get_memory_profile_client().request(
                    method=request.method,
                    path=path,
                    workspace_id=str(workspace_id),
                    tenant_id=str(current_user.tenant_id),
                    actor_id=_caller_actor_id(current_user, caller_source),
                    source=caller_source,
                    trace_id=trace_id,
                    language_type=language_type,
                    query=query,
                    raw=True,
                )
                if isinstance(result, MemoryProfileRawResponse):
                    # The upstream body is already a serialized ApiResponse;
                    # returning it directly bypasses response_model re-encoding.
                    return Response(
                        content=result.body,
                        media_type=result.media_type,
                        status_code=200,
                    )
                return result
            except MemoryProfileClientError as exc:
                logger.error(
                    "memory profile proxy failed: path=%s error=%s trace_id=%s",
                    path,
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
