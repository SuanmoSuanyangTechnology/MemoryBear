"""Service-owned plain-text PDF parser."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from ...context import ParsedBlock, ParsedBlockType, ParseResult
from ..base import DocumentParser

LOGGER = logging.getLogger(__name__)


class PlainPdfParser(DocumentParser):
    def parse(self, ctx) -> ParseResult:
        page_number: int | None = None
        try:
            reader = PdfReader(BytesIO(self._read_binary(ctx)))
            start = max(int(ctx.from_page), 0)
            end = min(max(int(ctx.to_page), 0), len(reader.pages))
            page_texts: list[str] = []
            for page_number in range(start, end):
                text = reader.pages[page_number].extract_text()
                if text and text.strip():
                    page_texts.append(text.strip())
        except Exception as exc:  # noqa: BLE001 - normalize third-party PDF errors.
            LOGGER.warning(
                "Plain PDF parsing failed page=%s error_type=%s",
                page_number,
                type(exc).__name__,
            )
            raise RuntimeError("Plain PDF parsing failed") from exc

        content = "\n\n".join(page_texts)
        if not content:
            LOGGER.warning("Plain PDF contains no extractable text")
            return ParseResult(blocks=[], merge_strategy="blocks")
        return ParseResult(
            blocks=[
                ParsedBlock(
                    type=ParsedBlockType.TEXT,
                    content=content,
                    seq=0,
                    start_line=1,
                    end_line=content.count("\n") + 1,
                )
            ],
            merge_strategy="blocks",
        )

    @staticmethod
    def _read_binary(ctx) -> bytes:
        if ctx.binary is not None:
            return ctx.binary
        return Path(ctx.filename).read_bytes()


__all__ = ["PlainPdfParser"]
