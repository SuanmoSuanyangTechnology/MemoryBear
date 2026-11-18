"""SSE forwarding for memory-profile routes whose response is a stream.

The JSON proxy in :mod:`app.integrations.profile.proxy` buffers one envelope and
returns it as a normal response. A migrated streaming endpoint cannot do that:
its body is a long-lived ``text/event-stream`` and re-buffering it would destroy
the incremental progress contract. This decorator forwards the upstream bytes as
they arrive, with the same identity resolution and header rules as the JSON
proxy, and keeps the local failure shape the streaming face already had — one
``error`` event instead of a broken connection.

Usage (the decorator must sit below the authentication/workspace guard so its
dependencies run first, and above the handler whose signature FastAPI reads)::

    @router.post("/read/stream", response_model=None)
    @cur_workspace_access_guard_self_db()
    @memory_profile_stream_proxy(
        body_arg="user_input",
        body_fields=("message", "search_switch", "end_user_id", "session_id"),
        target_path="/internal/v1/memory/read/stream",
    )
    async def read_server(request: Request, user_input: UserInput, ...): ...
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from functools import wraps
from inspect import signature
from typing import Any, ParamSpec, TypeVar
from uuid import uuid4

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.core.error_codes import BizCode
from app.core.trace import get_trace_id
from app.i18n.dependencies import get_current_language
from app.i18n.service import t

from .contracts import MemoryProfileAuthSource
from .errors import MemoryProfileClientError, MemoryProfileConfigurationError
from .proxy import _caller_source, _resolve_identity
from .runtime import get_memory_profile_client

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")

#: Same headers the host's own SSE endpoint sets (no proxy buffering, keep-alive).
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _error_frame(*, request_id: str, language: str) -> bytes:
    """One terminal SSE error event, matching the service's own framing."""

    payload = {
        "request_id": request_id,
        "code": int(BizCode.SERVICE_UNAVAILABLE),
        "message": t("memory.errors.profile_unavailable", language),
    }
    return (
        "event: error\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode()


def _payload_from_body(
    arguments: dict[str, Any], *, body_arg: str, body_fields: tuple[str, ...]
) -> dict[str, Any]:
    """Rebuild the forwarded body from the handler's validated request model."""

    if not body_fields:
        return {}
    model = arguments.get(body_arg)
    if model is None:
        raise MemoryProfileConfigurationError(
            f"memory_profile_stream_proxy body argument {body_arg!r} is missing"
        )
    if hasattr(model, "model_dump"):
        dumped = model.model_dump(mode="json")
        return {field: dumped.get(field) for field in body_fields}
    return {field: getattr(model, field, None) for field in body_fields}


def memory_profile_stream_proxy(
    *,
    body_arg: str = "user_input",
    body_fields: tuple[str, ...] = (),
    target_path: str | None = None,
    principal_arg: str = "current_user",
    source: MemoryProfileAuthSource | None = None,
    timeout_seconds: float | None = None,
):
    """Forward a streaming route to memory-profile for every request.

    ``body_fields`` are read off ``body_arg`` (a Pydantic request model) and sent
    as JSON; the forwarded path is ``target_path`` when the internal route differs
    from the caller's own path. ``source`` is derived from the caller path like the
    JSON proxy, and can be pinned for callers that have no such path.
    """

    def decorate(handler):
        handler_signature = signature(handler)

        @wraps(handler)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> StreamingResponse:
            if "request" in kwargs and "current_user" in kwargs:
                arguments = kwargs
            else:
                arguments = handler_signature.bind_partial(*args, **kwargs).arguments
            request = arguments.get("request")
            if not isinstance(request, Request):
                raise MemoryProfileConfigurationError(
                    "memory_profile_stream_proxy requires the handler to declare "
                    "request: Request"
                )
            current_user = arguments.get(principal_arg)
            if current_user is None:
                current_user = arguments.get("current_user")
            language = await get_current_language(request)
            caller_path = request.scope["path"]
            caller_source = (
                _caller_source(caller_path) if source is None else source
            )
            workspace_id, tenant_id, actor_id = _resolve_identity(
                current_user, caller_source
            )
            if workspace_id is None:
                raise MemoryProfileConfigurationError(
                    "memory_profile_stream_proxy requires a workspace context"
                )

            payload = _payload_from_body(
                arguments, body_arg=body_arg, body_fields=body_fields
            )
            trace_id = get_trace_id() or uuid4().hex
            path = target_path or caller_path
            client = get_memory_profile_client()

            async def frames() -> AsyncIterator[bytes]:
                try:
                    async for chunk in client.stream(
                        method=request.method,
                        path=path,
                        workspace_id=str(workspace_id),
                        tenant_id=str(tenant_id),
                        actor_id=actor_id,
                        source=caller_source,
                        trace_id=trace_id,
                        language_type=language,
                        payload=payload,
                        timeout=timeout_seconds,
                    ):
                        yield chunk
                except MemoryProfileClientError as exc:
                    logger.error(
                        "memory profile stream failed: path=%s error=%s trace_id=%s",
                        path,
                        type(exc).__name__,
                        trace_id,
                    )
                    yield _error_frame(request_id=trace_id, language=language)

            return StreamingResponse(
                frames(),
                media_type="text/event-stream",
                headers=dict(_SSE_HEADERS),
            )

        return wrapped

    return decorate


__all__ = ["memory_profile_stream_proxy"]