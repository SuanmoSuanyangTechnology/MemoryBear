"""Endpoint decorators for mandatory memory-profile forwarding."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import wraps
from inspect import signature
from typing import Any, ParamSpec, TypeVar
from uuid import UUID, uuid4

from fastapi import Request, Response

from app.core.error_codes import BizCode
from app.core.response_utils import fail
from app.core.trace import get_trace_id
from app.i18n.dependencies import get_current_language
from app.i18n.service import t

from .contracts import MemoryProfileAuthSource, MemoryProfileRawResponse
from .errors import MemoryProfileClientError, MemoryProfileConfigurationError
from .runtime import get_memory_profile_client

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


def _resolve_identity(
    current_user: Any, source: MemoryProfileAuthSource
) -> tuple[Any, Any, str]:
    """Resolve ``(workspace_id, tenant_id, actor_id)`` for either face.

    Handlers that run ``@require_api_key_self_db`` do not declare a
    ``current_user`` parameter, so the principal comes from the API-key context
    that decorator established. That context also names the actor; the two other
    values are still required and are validated by the caller.
    """

    if current_user is not None:
        return (
            getattr(current_user, "current_workspace_id", None),
            getattr(current_user, "tenant_id", None),
            _caller_actor_id(current_user, source),
        )
    from app.core.api_key_auth import get_current_api_key_auth

    try:
        auth = get_current_api_key_auth()
    except Exception as exc:  # BusinessException when no key authenticated
        raise MemoryProfileConfigurationError(
            "memory_profile_proxy requires current_user or an API-key context"
        ) from exc
    if source == MemoryProfileAuthSource.APIKEY:
        actor_id = str(auth.api_key_id)
    else:
        actor_id = str(auth.created_by)
    return auth.workspace_id, auth.tenant_id, actor_id


async def forward_memory_profile_request(
    *,
    method: str,
    path: str,
    current_user: Any,
    source: MemoryProfileAuthSource,
    query: Mapping[str, Any] | None = None,
    language_type: str | None = None,
    raw: bool = False,
) -> dict[str, Any] | MemoryProfileRawResponse:
    """Forward one subrequest to memory-profile from an endpoint that stays local.

    The dashboard aggregate keeps its own app/knowledge/API-call counters but
    embeds workspace statistics; this helper lets that block come from
    memory-profile without proxying the whole endpoint. ``path`` is the target
    internal route, while ``source`` names the face the caller reached us on —
    the two cannot be derived from each other, so the caller passes both. The
    acting-id rule is the same one :func:`memory_profile_proxy` applies.
    """

    workspace_id = getattr(current_user, "current_workspace_id", None)
    if workspace_id is None:
        raise MemoryProfileConfigurationError(
            "memory profile subrequest requires a workspace context"
        )
    return await get_memory_profile_client().request(
        method=method,
        path=path,
        workspace_id=str(workspace_id),
        tenant_id=str(current_user.tenant_id),
        actor_id=_caller_actor_id(current_user, source),
        source=source,
        trace_id=get_trace_id() or uuid4().hex,
        language_type=language_type,
        query=query or {},
        raw=raw,
    )


def memory_profile_proxy(
    *,
    query_fields: tuple[str, ...] = (),
    body_arg: str | None = None,
    body_fields: tuple[str, ...] = (),
    raw_body: bool = False,
    language_field: str | None = None,
    upper_bounds: dict[str, int] | None = None,
    source: MemoryProfileAuthSource | None = None,
    timeout_seconds: float | None = None,
    target_path: str | None = None,
    principal_arg: str = "current_user",
    preserve_status: bool = False,
):
    """Forward a migrated route to memory-profile for every request.
    The wrapped function defines the route signature, not a local fallback. The API-key
    routes run their authentication, workspace validation, and merge routing
    before calling it, so the decorator only replaces the final use-case call.
    Forwarding is path-driven: the handler declares ``request: Request`` and its
    caller path is forwarded, so adding an endpoint needs no entry here, and the
    source is read off that same path. Pass ``source`` to pin it for a caller that
    has no such path (a task or script invoking the handler directly); an explicit
    ``source`` wins over the path.

    A POST endpoint forwards a body rebuilt from ``body_fields``: each name is read
    off the argument named by ``body_arg`` (a Pydantic request model), or off the
    handler arguments when ``body_arg`` is omitted. Rebuilding rather than
    forwarding the raw body keeps merge routing intact — the API-key face may have
    rewritten ``end_user_id`` before this wrapper runs. The FastAPI-validated body
    model stays the single source of truth for what may cross the wire.

    ``target_path`` exists for the few legacy routes whose externally visible path
    differs from the one internal route that serves the capability (the admin hot-tags
    alias under ``/memory-storage``). Only the forwarded path moves; the caller's own
    path still decides the face (``x-source``) and the acting id.

    ``language_field`` is retained for existing decorators. Language selection
    belongs to the host's i18n middleware, including for those routes.

    ``raw_body`` forwards the request bytes instead of rebuilding a payload, so a
    body parsed inside the handler keeps its ``exclude_unset`` semantics.
    ``preserve_status`` returns the upstream HTTP status instead of forcing 200,
    for endpoints whose legacy contract distinguishes 4xx/5xx.

    ``principal_arg`` names the handler argument carrying the caller when it is
    not ``current_user``; when neither is present the API-key context established
    by ``@require_api_key_self_db`` supplies the identity.
    """

    bounds = dict(upper_bounds or {})

    def decorate(handler):
        handler_signature = signature(handler)

        @wraps(handler)
        async def wrapped(
            *args: P.args, **kwargs: P.kwargs
        ) -> R | dict[str, Any] | Response:
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
            current_user = arguments.get(principal_arg)
            if current_user is None:
                current_user = arguments.get("current_user")
            language = await get_current_language(request)
            workspace_id, tenant_id, actor_id = _resolve_identity(
                current_user,
                _caller_source(request.scope["path"]) if source is None else source,
            )
            if workspace_id is None:
                return fail(
                    BizCode.INVALID_PARAMETER,
                    t("memory.errors.workspace_required", language),
                    t("memory.errors.current_workspace_missing", language),
                )

            # The query is rebuilt from the declared fields rather than forwarded
            # raw: API-key callers reach the handler through merge routing, so the
            # caller's own end_user_id may still name a merged-away end user.
            # Non-primitive values (a ``UUID`` declared by the handler) are
            # stringified: httpx only encodes str/int/float/bool/None.
            query: dict[str, Any] = {}
            for field in query_fields:
                value = arguments.get(field)
                query[field] = str(value) if isinstance(value, UUID) else value
            for field, maximum in bounds.items():
                value = query.get(field)
                if value is not None and value > maximum:
                    query[field] = maximum

            payload = None
            raw_payload: bytes | None = None
            if raw_body:
                # Byte-for-byte passthrough: the handler on the other side parses
                # and validates the body itself, so unset fields must not become
                # explicit nulls here.
                raw_payload = await request.body()
            elif body_fields:
                if body_arg:
                    source_model = arguments.get(body_arg)
                    if source_model is None:
                        raise MemoryProfileConfigurationError(
                            f"memory_profile_proxy body argument {body_arg!r} is missing"
                        )
                    payload = {
                        field: getattr(source_model, field, None)
                        for field in body_fields
                    }
                else:
                    # ``arguments`` is a Mapping (``kwargs`` or
                    # ``bind_partial(...).arguments``), never an object with
                    # attributes: read the declared handler arguments by key, or
                    # every field would silently forward ``None``.
                    payload = {field: arguments.get(field) for field in body_fields}

            caller_path = request.scope["path"]
            path = target_path or caller_path
            # Forward the host's resolved language so local validation and the
            # profile response follow the same query/header preference order.
            trace_id = get_trace_id() or uuid4().hex
            caller_source = (
                _caller_source(caller_path) if source is None else source
            )
            try:
                result = await get_memory_profile_client().request(
                    method=request.method,
                    path=path,
                    workspace_id=str(workspace_id),
                    tenant_id=str(tenant_id),
                    actor_id=actor_id,
                    source=caller_source,
                    trace_id=trace_id,
                    language_type=language,
                    timezone=request.headers.get("X-Timezone"),
                    query=query,
                    payload=payload,
                    body=raw_payload,
                    raw_body=raw_body,
                    raw=True,
                    timeout=timeout_seconds,
                )
                if isinstance(result, MemoryProfileRawResponse):
                    # The upstream body is already a serialized ApiResponse;
                    # returning it directly bypasses response_model re-encoding.
                    return Response(
                        content=result.body,
                        media_type=result.media_type,
                        status_code=result.status_code if preserve_status else 200,
                        headers={"Content-Language": result.content_language or language},
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
                    t("memory.errors.profile_unavailable", language),
                    t("memory.errors.profile_unavailable_detail", language),
                )

        return wrapped

    return decorate
