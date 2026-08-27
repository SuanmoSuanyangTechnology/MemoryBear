from __future__ import annotations

import hashlib
import io
import ipaddress
import logging
import re
import unicodedata
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
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_USERINFO = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:-]+")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_IPV4_CANDIDATE = re.compile(r"[0-9.]+")
_LEGACY_IPV4_COMPONENT = re.compile(r"(?:[0-9]+|0[xX][0-9A-Fa-f]+)")


def _without_control_or_separator(value: str) -> str:
    return "".join(char for char in value if unicodedata.category(char)[0] not in {"C", "Z"})


def _has_unsafe_remote_char(value: str) -> bool:
    return any(char in {"%", "\\"} or unicodedata.category(char)[0] in {"C", "Z"} for char in value)


def _ascii_scheme_letters(value: str) -> str:
    return "".join(char for char in value if char.isascii() and char.isalpha()).lower()


def _raw_remote_authority(value: str) -> tuple[bool, str | None]:
    raw_scheme, separator, remainder = value.partition("://")
    if not separator:
        normalized = _without_control_or_separator(value)
        normalized_scheme, normalized_separator, _ = normalized.partition("://")
        return (
            bool(normalized_separator)
            and _ascii_scheme_letters(normalized_scheme) in {"http", "https"}
        ), None

    if _ascii_scheme_letters(raw_scheme) not in {"http", "https"}:
        return False, None
    if raw_scheme.lower() not in {"http", "https"}:
        return True, None

    authority = re.split(r"[/#?]", remainder, maxsplit=1)[0]
    if _has_unsafe_remote_char(raw_scheme) or _has_unsafe_remote_char(authority):
        return True, None
    return True, authority


def _valid_authority(parsed, authority: str) -> bool:
    if not authority or authority.count("@") > 1:
        return False

    host_port = authority
    if "@" in authority:
        userinfo, host_port = authority.split("@", 1)
        if not userinfo or not _USERINFO.fullmatch(userinfo):
            return False

    if host_port.startswith("["):
        closing_bracket = host_port.find("]")
        if closing_bracket <= 1 or host_port.count("[") != 1 or host_port.count("]") != 1:
            return False
        remainder = host_port[closing_bracket + 1 :]
        if remainder and (
            not remainder.startswith(":")
            or not remainder[1:].isascii()
            or not remainder[1:].isdigit()
        ):
            return False
    else:
        if "[" in host_port or "]" in host_port or host_port.count(":") > 1:
            return False
        if ":" in host_port:
            raw_host, raw_port = host_port.rsplit(":", 1)
            if not raw_host or not raw_port.isascii() or not raw_port.isdigit():
                return False

    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return bool(hostname) and (port is None or 0 <= port <= 65535)


def _is_legacy_ipv4_candidate(hostname: str) -> bool:
    parts = hostname.split(".")
    return 1 <= len(parts) <= 4 and all(_LEGACY_IPV4_COMPONENT.fullmatch(part) for part in parts)


def _normalize_remote_host(hostname: str) -> str | None:
    try:
        return ipaddress.ip_address(hostname).compressed.lower()
    except ValueError:
        if ":" in hostname:
            return None

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower().removesuffix(".")
    except UnicodeError:
        return None
    if not ascii_hostname:
        return None

    try:
        return ipaddress.ip_address(ascii_hostname).compressed.lower()
    except ValueError:
        if (
            ":" in ascii_hostname
            or _IPV4_CANDIDATE.fullmatch(ascii_hostname)
            or _is_legacy_ipv4_candidate(ascii_hostname)
        ):
            return None

    labels = ascii_hostname.split(".")
    if not all(label and _DNS_LABEL.fullmatch(label) for label in labels):
        return None
    return ascii_hostname if len(ascii_hostname) <= 253 else None


def safe_log_target(value: object) -> str:
    """Classify a target without logging user-controlled path or payload data."""
    if type(value) is not str:
        return "other-scheme"
    raw_value = value
    remote_candidate, raw_authority = _raw_remote_authority(raw_value)
    if remote_candidate and raw_authority is None:
        return "invalid-remote-target"
    if _WINDOWS_PATH.match(raw_value):
        return "local-file"
    try:
        parsed = urlsplit(raw_value)
    except ValueError:
        return "invalid-remote-target" if remote_candidate else "other-scheme"

    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        if (
            not remote_candidate
            or raw_authority is None
            or not _valid_authority(parsed, raw_authority)
        ):
            return "invalid-remote-target"
        hostname = _normalize_remote_host(parsed.hostname)
        return hostname or "invalid-remote-target"
    if scheme == "data":
        return "embedded-data"
    if scheme in {"", "file"}:
        return "local-file"
    return "other-scheme"


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
