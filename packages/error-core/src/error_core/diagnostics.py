"""Bounded, redacted diagnostic strings; never user-facing."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_ENTRIES = 5
_MAX_DETAIL = 2048
_MAX_TOTAL = 8192


def truncate_utf8(value: str, max_bytes: int, marker: str = "…") -> str:
    """Truncate on a UTF-8 byte budget without splitting a code point."""

    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= max_bytes:
        return encoded[:max_bytes].decode("utf-8", "ignore")
    return encoded[: max_bytes - len(marker_bytes)].decode("utf-8", "ignore") + marker

_KEY_VALUE = re.compile(
    r"(?i)([\"']?(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|token|secret)[\"']?\s*[:=]\s*)([\"']?)"
    r"(?:(?:bearer|basic)\s+)?[^\s,;\"']+"
)

_PLAIN_BEARER_WORDS = r"(?:tokens?|credentials?|auth(?:entication|orization)?|headers?)\b"

_REDACTIONS = (
    (_KEY_VALUE, r"\1\2[REDACTED]"),
    (
        re.compile(
            rf"(?i)\bbearer\s+(?!{_PLAIN_BEARER_WORDS})[^\s,;\"']+"
        ),
        "[REDACTED]",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[REDACTED_JWT]"),
)


def redact(value: str) -> str:
    cleaned = value
    for pattern, replacement in _REDACTIONS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


@dataclass(frozen=True)
class DiagnosticEntry:
    provider: str | None = None
    upstream_status: int | None = None
    upstream_code: str | None = None
    channel: str | None = None
    attempts: int | None = None
    trace_id: str | None = None
    detail: str = ""

    def render(self) -> str:
        fields: list[str] = []
        if self.provider:
            fields.append(f"provider={self.provider}")
        if self.upstream_status is not None:
            fields.append(f"upstream_status={self.upstream_status}")
        if self.upstream_code:
            fields.append(f"upstream_code={self.upstream_code}")
        if self.channel:
            fields.append(f"channel={self.channel}")
        if self.attempts is not None:
            fields.append(f"attempts={self.attempts}")
        if self.trace_id:
            fields.append(f"trace_id={self.trace_id}")
        if self.detail:
            escaped = redact(self.detail).replace("\\", "\\\\").replace('"', '\\"')
            detail = truncate_utf8(escaped, _MAX_DETAIL)
            if fields:
                fields[-1] = f'{fields[-1]}: "{detail}"'
            else:
                fields.append(f'detail: "{detail}"')
        return " ".join(fields)


def format_diagnostics(
    *,
    provider: str | None = None,
    upstream_status: int | None = None,
    upstream_code: str | None = None,
    channel: str | None = None,
    attempts: int | None = None,
    trace_id: str | None = None,
    detail: str = "",
) -> str:
    entry = DiagnosticEntry(
        provider=provider,
        upstream_status=upstream_status,
        upstream_code=upstream_code,
        channel=channel,
        attempts=attempts,
        trace_id=trace_id,
        detail=detail,
    )
    return truncate_utf8(entry.render(), _MAX_TOTAL)


def format_channel_diagnostics(
    entries: list[DiagnosticEntry],
    *,
    trace_id: str | None = None,
    detail: str = "",
) -> str:
    rendered = [text for entry in entries if (text := entry.render())]
    unique = list(dict.fromkeys(rendered))
    parts = unique[:_MAX_ENTRIES]
    omitted = len(unique) - len(parts)
    if omitted > 0:
        parts.append(f"... (+{omitted} more)")
    if detail:
        parts.append(truncate_utf8(redact(detail), _MAX_DETAIL))
    if trace_id:
        parts.append(f"trace_id={trace_id}")
    return truncate_utf8("; ".join(parts), _MAX_TOTAL)


__all__ = [
    "DiagnosticEntry",
    "format_channel_diagnostics",
    "format_diagnostics",
    "redact",
    "truncate_utf8",
]
