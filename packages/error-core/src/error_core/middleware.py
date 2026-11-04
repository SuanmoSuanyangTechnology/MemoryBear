"""Language negotiation middleware shared by FastAPI services.

This module requires Starlette/FastAPI, which every HTTP service already
installs; the rest of ``error_core`` stays dependency-free.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .i18n import resolve_locale
from .translations import set_current_locale


class LanguageContextMiddleware(BaseHTTPMiddleware):
    """Resolve the request language and echo it on the response.

    Services override :meth:`default_locale` to supply their configured
    fallback language.
    """

    def default_locale(self) -> str:
        return "zh"

    async def dispatch(self, request: Request, call_next) -> Response:
        language = resolve_locale(
            explicit=request.query_params.get("lang"),
            language_type=request.headers.get("X-Language-Type"),
            accepted=request.headers.get("Accept-Language"),
            default=self.default_locale(),
        )
        request.state.language = language
        set_current_locale(language)
        response = await call_next(request)
        response.headers["Content-Language"] = language
        return response


__all__ = ["LanguageContextMiddleware"]
