"""AppChatService adapter: typed carriage for the shared chat pipeline.

All chat semantics (key gate, preparation, SSE framing, error vocabulary) live
in ``services.chat_use_case``; this layer only decodes the envelope into the
same ``AppChatRequest`` the HTTP route builds, delivers structured frames, and
converts pre-stream failures into gRPC status + HTTP-restoring trailers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from memorybear.application.v1 import chat_pb2, chat_pb2_grpc
from memorybear_grpc.interceptors import current_principal
from memorybear_grpc.metadata import bind_upstream_remaining, reset_upstream_remaining
from memorybear_grpc.request import MetadataRequest
from pydantic import ValidationError

from ..api.error_handlers import validation_error_content
from ..api.routes.messages import (
    ChatRequestValidationError,
    chat_validation_content,
    validate_chat_body,
)
from ..api.schemas.common import fail
from ..errors import BizCode, BusinessException, as_biz_code, http_status_for
from ..i18n import resolve_locale, translate
from ..schemas.app_schema import AppChatRequest
from ..sensitive import SensitiveDataFilter
from ..services.chat_use_case import (
    chat_complete,
    chat_events,
    prepare_chat,
    resolve_app_for_chat,
)
from ..trace import TRACE_ID_HEADER, normalize_trace_id, reset_trace_id, set_trace_id
from .auth import APP_CHAT_ACL_METHOD, APP_CHAT_ACL_PATH, AppChatPrincipal
from .envelope import abort_with, auth_rejection_content, json_bytes

logger = logging.getLogger(__name__)


class _RequestRejected(Exception):
    """Pre-stream rejection carrying the HTTP status and envelope to restore."""

    def __init__(self, http_status: int, content: dict[str, Any]) -> None:
        super().__init__(str(content.get("msg") or ""))
        self.http_status = http_status
        self.content = content


def _decode_body(body_json: bytes, *, language: str) -> AppChatRequest:
    """Proto bytes -> the same request object the HTTP route builds."""

    try:
        body = json.loads(body_json)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _RequestRejected(
            422, chat_validation_content("请求体不是合法的 JSON")
        ) from exc
    try:
        return validate_chat_body(body)
    except ChatRequestValidationError as exc:
        raise _RequestRejected(422, chat_validation_content(exc.detail)) from exc
    except ValidationError as exc:
        raise _RequestRejected(422, validation_error_content(exc, language)) from exc


def _business_failure(exc: BusinessException) -> tuple[int, dict[str, Any]]:
    """Business error -> the same status + envelope the HTTP handler renders."""

    message, _context = SensitiveDataFilter.filter_message(exc.message, exc.context)
    biz_code = as_biz_code(exc.code)
    return http_status_for(biz_code), fail(
        code=biz_code.value, msg=message, error=message
    )


class ChatServicer(chat_pb2_grpc.AppChatServiceServicer):
    """Stateless adapter: the shared pipeline resolves everything per call."""

    async def Chat(  # noqa: N802 - generated method name
        self, request: chat_pb2.ChatRequest, context
    ) -> AsyncIterator[chat_pb2.ChatFrame]:
        token = bind_upstream_remaining(context.time_remaining())
        try:
            view = MetadataRequest(
                context.invocation_metadata(),
                method=APP_CHAT_ACL_METHOD,
                path=APP_CHAT_ACL_PATH,
            )
            language = resolve_locale(accepted=view.headers.get("accept-language"))
            # Same contract as the HTTP trace middleware: a missing or
            # malformed trace id becomes a generated one, and the service
            # ContextVar stays bound for the whole stream.
            trace_token = set_trace_id(
                normalize_trace_id(view.headers.get(TRACE_ID_HEADER))
            )
            try:
                principal = current_principal()
                if not isinstance(principal, AppChatPrincipal):
                    await abort_with(
                        context,
                        401,
                        auth_rejection_content(401, "missing principal", language),
                    )
                try:
                    # Host route order: the key binding, publish state and
                    # scope are re-applied before the body is even read.
                    app_id = await resolve_app_for_chat(
                        api_key_id=principal.api_key_id,
                        workspace_id=principal.workspace_id,
                    )
                    workspace_id = principal.workspace_id
                    if workspace_id is None:
                        # resolve_app_for_chat can only succeed inside a
                        # workspace; this guard narrows the non-optional
                        # use-case signature (unreachable).
                        raise BusinessException(
                            "API Key 无效或已过期", BizCode.API_KEY_INVALID
                        )
                    payload = _decode_body(request.body_json, language=language)
                    prep = await prepare_chat(
                        app_id=app_id, workspace_id=workspace_id, payload=payload
                    )
                except _RequestRejected as exc:
                    await abort_with(context, exc.http_status, exc.content)
                except BusinessException as exc:
                    http_status, content = _business_failure(exc)
                    await abort_with(context, http_status, content)
                except Exception:
                    logger.exception("chat preparation failed")
                    message = translate("errors.common.internal_error", language)
                    await abort_with(
                        context,
                        500,
                        fail(BizCode.INTERNAL_ERROR.value, message, error=message),
                    )

                if not payload.stream:
                    try:
                        body = await chat_complete(prep)
                    except BusinessException as exc:
                        http_status, content = _business_failure(exc)
                        await abort_with(context, http_status, content)
                    except Exception:
                        logger.exception("chat completion failed")
                        message = translate("errors.common.internal_error", language)
                        await abort_with(
                            context,
                            500,
                            fail(BizCode.INTERNAL_ERROR.value, message, error=message),
                        )
                    yield chat_pb2.ChatFrame(
                        seq=0,
                        result=chat_pb2.ResultFrame(
                            http_status=200, body_json=json_bytes(body)
                        ),
                    )
                    return

                # Streaming: every frame is one SSE event with keepalive comments
                # removed (the gateway generates its own heartbeats downstream).
                # A failure after the first frame truncates the stream exactly
                # like the HTTP SSE path (no synthetic frame is appended).
                seq = 0
                started = False
                events = chat_events(prep)
                try:
                    async for event, data in events:
                        started = True
                        yield chat_pb2.ChatFrame(
                            seq=seq,
                            event=chat_pb2.EventFrame(event=event, data_json=data),
                        )
                        seq += 1
                except BusinessException as exc:
                    if not started:
                        http_status, content = _business_failure(exc)
                        await abort_with(context, http_status, content)
                    logger.warning("chat stream failed mid-flight: %s", exc)
                except Exception:
                    if not started:
                        logger.exception("chat stream failed before the first frame")
                        message = translate("errors.common.internal_error", language)
                        await abort_with(
                            context,
                            500,
                            fail(BizCode.INTERNAL_ERROR.value, message, error=message),
                        )
                    logger.exception("chat stream failed mid-flight")
                finally:
                    await events.aclose()
            finally:
                reset_trace_id(trace_token)
        finally:
            reset_upstream_remaining(token)


__all__ = ["ChatServicer"]
