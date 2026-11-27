"""Request-local ordered tool-call steps for assistant Message metadata.

Independent of :mod:`message_trace` on purpose. That module models the
long-term-memory product protocol (``meta_data.memory_retrieval``) and filters
every other tool out; this collector records *all* tool calls of one
assistant turn, in the order they actually happened, so the chat timeline can
render the same order after a refresh.

A turn's visible body is one text paragraph per tool call, so the collector also
records *text segments*: consecutive ``message`` chunks share one segment, and a
tool call opens the next. A segment stores only its own metadata
(``type``/``length``) and never the text itself — the body stays in the message
``content`` and the client slices it by offset, so persistence never stores the
reply twice.

The class is pure state: feed events in, take a snapshot out. No DB, no IO, no
event names — every SSE emitting site (agent chat, cluster chat, draft run,
compare/regenerate) reuses it verbatim.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Any

MAX_STEPS = 100
MAX_DATA_CHARS = 2000
MAX_ERROR_CHARS = 500
# Markers on the non-tool steps. Tool steps carry no ``type``: their shape
# predates text segments and stays unchanged.
TEXT_STEP_TYPE = "text"
# A sub-agent dispatch (cluster only). It is a step in the same order as text and
# tool calls — the supervisor narrates, dispatches an agent, narrates again — so
# the timeline must be able to place the agent card between two paragraphs.
AGENT_STEP_TYPE = "agent"

# Steps whose name matches are dropped from the ordered timeline. Empty today:
# the long-term-memory tool used to sit here because ``memory_retrieval`` was its
# only panel, but the call now takes a ``seq`` like any other tool so the client
# can place the memory card in timeline order. The product payload stays
# additional context, not a second timeline entry.
_SKIP_NAMES: set[str] = set()


def _limit_payload(value: Any, limit: int) -> Any:
    """Keep the original JSON shape while it fits, otherwise clip to text."""
    if value is None:
        return None
    if isinstance(value, str):
        return value[:limit]
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)[:limit]
    if len(text) <= limit:
        return value
    return text[:limit]


def _utf16_len(text: str) -> int:
    """Length in UTF-16 code units — the same unit as ``String.length`` in JS.

    The client slices the body by these offsets, so emoji (one code point, two
    code units in JS) must be counted the way the client counts them.
    """
    return len(text.encode("utf-16-le")) // 2


class ToolStepCollector:
    """Accumulate this turn's tool calls in first-seen order."""

    def __init__(self) -> None:
        self._steps: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._started_at: dict[str, float] = {}
        # Text segments, in seq order. Kept apart from ``_steps`` so the tool
        # budget and the tool-only ``has_steps`` semantics stay untouched.
        self._text: list[dict[str, Any]] = []
        # Text of each segment, kept in memory only: it rides the ``end`` frame
        # (``last_message``) and never enters the persisted snapshot, so the body
        # is still stored exactly once, in the message ``content``.
        self._text_content: dict[int, str] = {}
        self._open_text_seq: int | None = None
        self._seq = 0
        self._truncated = False

    def start(
        self,
        *,
        step_id: str | None,
        name: str | None,
        input_value: Any = None,
        record_clock: bool = True,
        step_type: str | None = None,
    ) -> int | None:
        """Record a tool start; return the seq assigned to this step.

        Returns ``None`` when the step is filtered, when ``step_id``
        is missing, or when the step budget is exhausted — callers must still
        forward the raw event in that case.

        ``record_clock=False`` is for replayed steps whose real timing happened
        before this collector existed (weak-model catch-up); such a step gets no
        ``duration_ms`` instead of a misleading ~0ms.

        ``step_type`` marks a non-tool step (a cluster sub-agent dispatch): it is
        keyed by the agent execution id and keeps the same ordering machinery, so
        the timeline can interleave agent cards with text and tool cards.
        """
        key = str(step_id or "")
        if not key or (step_type is None and name in _SKIP_NAMES):
            return None

        # A tool call ends the paragraph before it, so the chunks after it open a
        # new text segment.
        self._open_text_seq = None

        existing = self._steps.get(key)
        if existing is not None:
            # Replays (weak-model catch-up) re-send the same step; keep the
            # original seq and status, only refresh the input if provided.
            if input_value is not None:
                existing["input"] = _limit_payload(input_value, MAX_DATA_CHARS)
            return existing["seq"]

        if len(self._steps) >= MAX_STEPS:
            self._truncated = True
            return None

        self._seq += 1
        step: dict[str, Any] = {"seq": self._seq}
        if step_type is not None:
            # Only non-tool steps carry a marker; tool steps keep their
            # historical shape (no ``type`` key) so old payloads stay identical.
            step["type"] = step_type
        step["step_id"] = key
        step["name"] = name or ""
        step["status"] = "running"
        if input_value is not None:
            step["input"] = _limit_payload(input_value, MAX_DATA_CHARS)
        self._steps[key] = step
        if record_clock:
            self._started_at[key] = time.monotonic()
        return self._seq

    def finish(
        self,
        *,
        step_id: str | None,
        output: Any = None,
        error: Any = None,
    ) -> int | None:
        """Close a step as completed (``error`` empty) or failed.

        Returns ``duration_ms`` when it can be measured, else ``None`` (unknown
        step, already closed, or the start was never observed here).
        """
        key = str(step_id or "")
        step = self._steps.get(key)
        if step is None:
            return None

        failed = error not in (None, "")
        step["status"] = "failed" if failed else "completed"
        if output is not None:
            step["output"] = _limit_payload(output, MAX_DATA_CHARS)
        if failed:
            step["error"] = str(error)[:MAX_ERROR_CHARS]

        duration = self._close_clock(key)
        if duration is not None:
            step["duration_ms"] = duration
        return duration

    def text(self, chunk: str) -> int | None:
        """Record one body-text chunk; return the seq of the segment it joined.

        Consecutive chunks share a segment, and a segment only opens after a tool
        call interrupted the text, so the segment count is bounded by the number
        of tool steps plus one — a 500-token answer is still a single step.

        ``length`` is the UTF-16 length of the chunk (see :func:`_utf16_len`), so
        the client can walk the body by offset without drifting on emoji.
        """
        if not chunk:
            return None

        if self._open_text_seq is not None and self._text:
            last = self._text[-1]
            if last["seq"] == self._open_text_seq:
                last["length"] = int(last["length"]) + _utf16_len(chunk)
                self._text_content[self._open_text_seq] = (
                    self._text_content.get(self._open_text_seq, "") + chunk
                )
                return self._open_text_seq

        self._seq += 1
        self._text.append(
            {"seq": self._seq, "type": TEXT_STEP_TYPE, "length": _utf16_len(chunk)}
        )
        self._text_content[self._seq] = chunk
        self._open_text_seq = self._seq
        return self._seq

    def reconcile(self, node_executions: list[dict[str, Any]] | None) -> None:
        """Close steps the agent aborted without a tool_end/tool_error.

        Only steps still ``running`` are touched; anything already terminal keeps
        the state and duration it was given by :meth:`finish`.
        """
        for entry in node_executions or []:
            key = str(entry.get("step_id") or "")
            step = self._steps.get(key)
            if step is None or step["status"] != "running":
                continue
            error = entry.get("error")
            if entry.get("status") == "failed" or error:
                step["status"] = "failed"
                if error:
                    step["error"] = str(error)[:MAX_ERROR_CHARS]
            else:
                step["status"] = "completed"
            if step.get("input") is None and entry.get("input") is not None:
                step["input"] = _limit_payload(entry["input"], MAX_DATA_CHARS)
            if step.get("output") is None and entry.get("output") is not None:
                step["output"] = _limit_payload(entry["output"], MAX_DATA_CHARS)
            duration = self._close_clock(key)
            if duration is not None:
                step["duration_ms"] = duration

    def seq_of(self, step_id: str | None) -> int | None:
        """Seq of an already-recorded step (used to tag terminal events)."""
        step = self._steps.get(str(step_id or ""))
        return step["seq"] if step else None

    def last_seq(self) -> int | None:
        """Highest seq allocated this turn, or ``None`` when nothing was opened.

        Carried on the ``end`` frame as ``last_seq``: the client closes the turn
        with it, so the final body segment can be attributed without trusting
        arrival order of the last ``message`` chunk. Only allocated seqs count —
        a turn that called no tool and streamed no body has nothing to report.
        """
        return self._seq or None

    def last_message(self) -> dict[str, Any] | None:
        """Full text of this turn's last body segment, or ``None`` when no text.

        Carried on the ``end`` frame as ``last_message``: ``seq`` is the segment's
        own seq (the number its ``message`` chunks already carried) and ``content``
        is that whole segment, so the client can close the reply with the text
        itself instead of stitching the chunks back together.

        It is the last *text* segment: when a turn ends on a tool call, ``seq`` is
        lower than :meth:`last_seq`. The text is held in memory only — the message
        ``content`` stays the single stored copy.
        """
        if not self._text:
            return None
        seq = int(self._text[-1]["seq"])
        content = self._text_content.get(seq)
        if content is None:
            return None
        return {"seq": seq, "content": content}

    def has_steps(self) -> bool:
        return bool(self._steps)

    def build(self) -> dict[str, Any] | None:
        """Text segments and terminal tool steps as one ordered list.

        ``None`` when the turn called no tool: with no interleaving to replay the
        client keeps rendering the body as a single run of text, exactly as
        before this feature.
        """
        steps = [
            dict(step)
            for step in self._steps.values()
            if step.get("status") in {"completed", "failed"}
        ]
        if not steps:
            return None
        # Text segments are snapshots already; merge and restore arrival order.
        steps.extend(dict(step) for step in self._text)
        steps.sort(key=lambda step: step["seq"])
        payload: dict[str, Any] = {"schema_version": 1, "steps": steps}
        if self._truncated:
            payload["truncated"] = True
        return payload

    def _close_clock(self, key: str) -> int | None:
        started = self._started_at.pop(key, None)
        if started is None:
            return None
        return max(0, int((time.monotonic() - started) * 1000))


def apply_tool_event(
    collector: ToolStepCollector,
    event_name: str,
    data: dict[str, Any] | None,
    *,
    replay: bool = False,
) -> dict[str, Any]:
    """Feed one ``tool_*`` payload into the collector, return the payload to emit.

    The result is a copy of ``data`` plus ``seq`` (all three events) and
    ``duration_ms`` (terminal events, only when measurable). The input dict is
    never mutated, and unknown event names are passed through untouched.
    """
    payload = dict(data or {})
    if event_name not in {"tool_start", "tool_end", "tool_error"}:
        return payload

    step_id = payload.get("step_id")
    if event_name == "tool_start":
        seq = collector.start(
            step_id=step_id,
            name=payload.get("name"),
            input_value=payload.get("input"),
            record_clock=not replay,
        )
    else:
        duration = collector.finish(
            step_id=step_id,
            output=payload.get("output"),
            error=payload.get("error"),
        )
        seq = collector.seq_of(step_id)
        if duration is not None:
            payload["duration_ms"] = duration

    if seq is not None:
        payload["seq"] = seq
    return payload


def apply_text_event(
    collector: ToolStepCollector,
    data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Feed one ``message`` payload into the collector, return the payload to emit.

    The payload keeps its fields and gains ``seq``: the text segment this chunk
    belongs to. A chunk with no content passes through untouched, and a failure
    to allocate never drops the chunk — text is never disposable.
    """
    payload = dict(data or {})
    content = payload.get("content")
    if isinstance(content, str) and content:
        seq = collector.text(content)
        if seq is not None:
            payload["seq"] = seq
    return payload


def apply_agent_event(
    collector: ToolStepCollector,
    event_name: str,
    data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Feed one ``agent_dispatch`` / ``agent_complete`` payload, return the emit payload.

    A cluster supervisor calling a sub-agent is a step of the same turn, so it
    takes a ``seq`` exactly like a tool call: the supervisor narrates, dispatches
    an agent, narrates again, and the timeline has to place that agent card
    between the two paragraphs. The step is keyed by ``execution_id`` (the same id
    the sub-agent block uses), and the sub-agent's own internals stay untouched.
    """
    payload = dict(data or {})
    if event_name not in {"agent_dispatch", "agent_complete"}:
        return payload

    execution_id = payload.get("execution_id")
    if event_name == "agent_dispatch":
        seq = collector.start(
            step_id=execution_id,
            name=payload.get("agent_name"),
            input_value=payload.get("task"),
            step_type=AGENT_STEP_TYPE,
        )
    else:
        duration = collector.finish(
            step_id=execution_id,
            output=payload.get("output"),
            error=payload.get("error"),
        )
        seq = collector.seq_of(execution_id)
        if duration is not None:
            payload["duration_ms"] = duration

    if seq is not None:
        payload["seq"] = seq
    return payload


def merge_tool_steps(meta: dict[str, Any], collector: ToolStepCollector) -> dict[str, Any]:
    """Attach the collector snapshot to assistant metadata (no-op when empty).

    Persisted as ``meta_data.timeline``: the payload holds text segments and
    sub-agent dispatches too, so a tool-only name would misdescribe it.
    """
    merged = dict(meta or {})
    snapshot = collector.build()
    if snapshot is not None:
        merged["timeline"] = snapshot
    return merged


def utf16_slice(text: str, start: int, end: int) -> str:
    """Slice ``text`` by UTF-16 code-unit offsets — the unit :func:`_utf16_len` counts."""
    encoded = text.encode("utf-16-le")
    return encoded[start * 2:end * 2].decode("utf-16-le")


def hydrate_text_steps(
    payload: dict[str, Any] | None,
    content: str | None,
) -> dict[str, Any] | None:
    """Return a copy of ``payload`` whose text steps carry their own text.

    Storage keeps only ``length`` per text segment: the body lives in the message
    ``content`` and the consumer slices it. A read API that already holds both
    halves can hand out the sliced text instead, so the consumer renders
    ``step["content"]`` and never touches offsets. ``length`` is kept — it is
    what places any text trailing the last segment.

    Nothing is guessed. When the recorded lengths do not fit the body (a
    regenerated answer, a moderation-preset replacement) the payload comes back
    untouched, so the caller falls back to slicing on its own.
    """
    steps = (payload or {}).get("steps")
    if not isinstance(steps, list) or not steps or not isinstance(content, str):
        return payload
    total = sum(
        int(step.get("length") or 0)
        for step in steps
        if isinstance(step, dict) and step.get("type") == TEXT_STEP_TYPE
    )
    if total <= 0 or total > _utf16_len(content):
        return payload
    hydrated: list[Any] = []
    cursor = 0
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != TEXT_STEP_TYPE:
            hydrated.append(dict(step) if isinstance(step, dict) else step)
            continue
        length = int(step.get("length") or 0)
        try:
            text = utf16_slice(content, cursor, cursor + length)
        except UnicodeDecodeError:
            # A segment boundary fell inside a surrogate pair: the recording and
            # the body disagree, so leave the payload alone.
            return payload
        cursor += length
        hydrated.append({**step, "content": text})
    return {**payload, "steps": hydrated}


def hydrate_message_meta(
    meta: dict[str, Any] | None,
    content: str | None,
) -> dict[str, Any] | None:
    """:func:`hydrate_text_steps` applied to a message's ``meta_data``.

    Read APIs that hand a message out (log detail, share view, v1 history) all
    carry both the recorded steps and the body, so all of them ship the sliced
    text. A message without steps, or with a non-dict meta, comes back as-is.
    """
    if not isinstance(meta, dict) or meta.get("timeline") is None:
        return meta
    return {**meta, "timeline": hydrate_text_steps(meta.get("timeline"), content)}