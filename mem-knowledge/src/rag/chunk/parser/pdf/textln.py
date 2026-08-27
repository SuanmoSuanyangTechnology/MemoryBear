"""TextLn PDF-to-structured-Markdown adapter."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

from .....bootstrap import get_settings
from ...context import ParseResult
from ..base import DocumentParser
from ..structured_markdown import StructMarkdownParser

LOGGER = logging.getLogger(__name__)


class TextLnPdfParser(DocumentParser):
    def __init__(
        self,
        *,
        api_server: str | None = None,
        app_id: str | None = None,
        secret_code: str | None = None,
        request_timeout_seconds: float | None = None,
        session=requests,
    ) -> None:
        settings = get_settings()
        self._api_server = api_server or settings.textln_apiserver
        self._app_id = (
            app_id if app_id is not None else settings.textln_app_id.get_secret_value()
        )
        self._secret_code = (
            secret_code
            if secret_code is not None
            else settings.textln_secret_code.get_secret_value()
        )
        self._request_timeout_seconds = (
            request_timeout_seconds or settings.textln_request_timeout_seconds
        )
        self._session = session

    def parse(self, ctx) -> ParseResult:
        binary = self._read_binary(ctx)
        self._callback(ctx, 0.15, "Use TextLn to recognize the PDF.")
        try:
            response = self._session.post(
                self._api_server,
                params={
                    "dpi": "144",
                    "get_image": "objects",
                    "markdown_details": "1",
                    "page_count": "1000",
                    "parse_mode": "auto",
                    "table_flavor": "md",
                },
                headers={
                    "x-ti-app-id": self._app_id,
                    "x-ti-secret-code": self._secret_code,
                    "Content-Type": "application/octet-stream",
                },
                data=binary,
                timeout=self._request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" status={status}" if status is not None else ""
            raise RuntimeError(f"TextLn request failed{suffix}") from None
        except ValueError:
            raise RuntimeError("TextLn returned invalid JSON") from None

        markdown = self._markdown(payload)
        if not markdown.strip():
            LOGGER.warning("TextLn returned empty Markdown")
            self._callback(ctx, 0.8, "TextLn returned empty content.")
            return ParseResult(direct_result=[], append_embed=False)
        blocks = StructMarkdownParser().parse_text(
            markdown,
            normalize_escaped_structure=True,
        )
        self._callback(ctx, 0.8, "Finish TextLn parsing.")
        return ParseResult(
            blocks=blocks,
            merge_strategy="blocks",
            markdown_preprocess_profile="textln",
            structured_markdown_stream=True,
        )

    @staticmethod
    def _markdown(payload: Any) -> str:
        result = payload.get("result") if isinstance(payload, dict) else None
        markdown = result.get("markdown") if isinstance(result, dict) else None
        if markdown is None:
            raise RuntimeError("TextLn response omitted result.markdown")
        if not isinstance(markdown, str):
            raise RuntimeError("TextLn result.markdown is not text")
        return markdown

    @staticmethod
    def _read_binary(ctx) -> bytes:
        if ctx.binary is not None:
            return ctx.binary
        return Path(ctx.filename).read_bytes()

    @staticmethod
    def _callback(ctx, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)


__all__ = ["TextLnPdfParser"]
