"""Resolve the request language once and expose it on ``request.state``.

Implemented as pure ASGI middleware: the hot path only reads two headers,
writes one scope key, and sets one response header. ``BaseHTTPMiddleware``
would add a task group and a streaming wrapper to every request for the same
work, which is measurable on a service whose cached path is sub-10ms.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.i18n.language import (
    ACCEPT_LANGUAGE_HEADER,
    LANGUAGE_HEADER,
    resolve_language,
)


class LanguageMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        language = resolve_language(
            headers.get(LANGUAGE_HEADER),
            headers.get(ACCEPT_LANGUAGE_HEADER),
        )
        scope.setdefault("state", {})["language"] = language

        async def send_with_language(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).setdefault("Content-Language", language)
            await send(message)

        await self.app(scope, receive, send_with_language)
