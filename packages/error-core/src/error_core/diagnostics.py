"""Bounded, redacted diagnostic strings; never user-facing."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_ENTRIES = 5
_MAX_DETAIL = 2048
_MAX_TOTAL = 8192

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
            detail = redact(self.detail)[:_MAX_DETAIL]
            escaped = detail.replace("\\", "\\\\").replace('"', '\\"')
            if fields:
                fields[-1] = f'{fields[-1]}: "{escaped}"'
            else:
                fields.append(f'detail: "{escaped}"')
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
    return entry.render()[:_MAX_TOTAL]


def format_channel_diagnostics(
    entries: list[DiagnosticEntry],
    *,
    trace_id: str | None = None,
    detail: str = "",
) -> str:
    shown = entries[:_MAX_ENTRIES]
    parts = [entry.render() for entry in shown if entry.render()]
    if len(entries) > _MAX_ENTRIES:
        parts.append(f"... (+{len(entries) - _MAX_ENTRIES} more)")
    if detail:
        parts.append(redact(detail)[:_MAX_DETAIL])
    if trace_id:
        parts.append(f"trace_id={trace_id}")
    return "; ".join(parts)[:_MAX_TOTAL]


__all__ = [
    "DiagnosticEntry",
    "format_channel_diagnostics",
    "format_diagnostics",
    "redact",
]
