from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .context import ChunkContext

_EMBED_PREFIXES = (
    "word/embeddings/",
    "word/objects/",
    "word/activex/",
    "xl/embeddings/",
    "ppt/embeddings/",
)
_LOG_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def safe_log_target(value: str) -> str:
    """Return a log-safe host/path without URL credentials or request parameters."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "invalid-target"
    if parsed.scheme.lower() in {"http", "https"}:
        host = parsed.hostname or "unknown-host"
        target = f"{host}{parsed.path or '/'}"
    else:
        target = parsed.path or Path(value).name or "unknown-path"
    return _LOG_CONTROL_CHARS.sub("?", target)


def _embedded_files(binary: bytes) -> list[tuple[str, bytes]]:
    if not binary.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return []
    result = []
    seen = set()
    try:
        with zipfile.ZipFile(io.BytesIO(binary)) as archive:
            for name in archive.namelist():
                if not name.lower().startswith(_EMBED_PREFIXES):
                    continue
                payload = archive.read(name)
                digest = hashlib.sha256(payload).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                result.append((Path(name).name, payload))
    except (OSError, zipfile.BadZipFile):
        return []
    return result


def _download_html(url: str, max_retries: int = 2) -> bytes | None:
    for _attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.content
        except (requests.Timeout, requests.RequestException):
            continue
    return None


class EmbedPreprocessor:
    def collect(self, ctx: ChunkContext, run_child) -> list:
        if not ctx.is_root:
            return []
        if ctx.binary is None:
            raise ValueError("Embedding extraction from file path is not supported.")

        result = []
        for filename, binary in _embedded_files(ctx.binary):
            try:
                result.extend(
                    run_child(
                        filename,
                        binary=binary,
                        ctx=ctx,
                        is_root=False,
                    )
                    or []
                )
            except Exception as exc:
                if ctx.callback:
                    ctx.callback(0.05, f"Failed to chunk embed {filename}: {exc}")
        return result


class HyperlinkPreprocessor:
    def collect_url_chunks(self, ctx: ChunkContext, urls, run_child) -> list:
        if not urls or not ctx.parser_config.get("analyze_hyperlink", False) or not ctx.is_root:
            return []

        result = []
        for index, url in enumerate(urls):
            html_bytes = _download_html(url)
            if not html_bytes:
                continue
            try:
                child_result = run_child(url, binary=html_bytes, ctx=ctx, is_root=False)
            except Exception as exc:
                logging.info(
                    "Failed to chunk registered URL target=%s error_type=%s",
                    safe_log_target(url),
                    type(exc).__name__,
                )
                child_result = run_child(
                    f"{index}.html",
                    binary=html_bytes,
                    ctx=ctx,
                    is_root=False,
                    vision_model=ctx.vision_model,
                )
            result.extend(child_result)
        return result


__all__ = ["EmbedPreprocessor", "HyperlinkPreprocessor", "safe_log_target"]
