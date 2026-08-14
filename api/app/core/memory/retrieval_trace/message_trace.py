"""Request-local memory retrieval trace for assistant Message metadata."""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from app.core.memory.retrieval_trace.stage_events import build_memory_stage_payload


def normalize_tool_input(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def strip_memory_trace_transients(value: Any) -> Any:
    """Remove request-only stage data before agent execution persistence."""
    if isinstance(value, dict):
        return {
            key: strip_memory_trace_transients(item)
            for key, item in value.items()
            if key != "_memory_stages"
        }
    if isinstance(value, list):
        return [strip_memory_trace_transients(item) for item in value]
    return value


class MessageTrace:
    """Accumulate display-safe retrieval stages for one assistant message."""

    def __init__(self) -> None:
        self._calls: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def start_tool(self, *, step_id: str | None, name: str, input_value: Any) -> None:
        # memory_retrieval 是长期记忆工具的产品协议，其他工具仍只保留 agent execution 轨迹。
        if not step_id or name != "long_term_memory":
            return
        self._calls.setdefault(
            str(step_id),
            {"name": name, "input": normalize_tool_input(input_value), "status": "running", "stages": []},
        )

    def add_stage(
        self,
        *,
        step_id: str | None,
        stage: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not step_id or str(step_id) not in self._calls:
            return None
        try:
            payload = build_memory_stage_payload(
                stage=str(stage.get("stage") or ""),
                data=stage.get("data") or {},
            )
        except (TypeError, ValueError):
            return None
        if payload is None:
            return None
        self._calls[str(step_id)]["stages"].append(payload)
        return payload

    def finish_tool(self, *, step_id: str | None, status: str, error: Any = None) -> None:
        if not step_id or str(step_id) not in self._calls:
            return
        call = self._calls[str(step_id)]
        call["status"] = "failed" if status in {"failed", "error"} else "completed"
        if call["status"] == "failed" and error:
            call["error"] = str(error)[:500]

    def record_weak_tool(self, step: dict[str, Any]) -> list[dict[str, Any]]:
        if step.get("node_name") != "long_term_memory":
            return []
        step_id = str(step.get("step_id") or "")
        self.start_tool(step_id=step_id, name="long_term_memory", input_value=step.get("input"))
        stages: list[dict[str, Any]] = []
        for stage in step.get("_memory_stages") or []:
            payload = self.add_stage(step_id=step_id, stage=stage)
            if payload is not None:
                stages.append(payload)
        self.finish_tool(step_id=step_id, status=step.get("status", "completed"), error=step.get("error"))
        return stages

    def reconcile_tools(self, steps: list[dict[str, Any]]) -> None:
        """Close traces when an Agent terminates without emitting tool_end/error."""
        for step in steps:
            step_id = str(step.get("step_id") or "")
            status = step.get("status")
            if step_id not in self._calls or status != "failed":
                continue
            self.finish_tool(
                step_id=step_id,
                status=status,
                error=step.get("error"),
            )

    def build(self) -> dict[str, Any] | None:
        """Build metadata only from terminal calls so incomplete traces are not exposed."""
        tool_calls = [
            call
            for call in self._calls.values()
            if call.get("status") in {"completed", "failed"}
        ]
        if not tool_calls:
            return None
        return {"schema_version": 1, "tool_calls": tool_calls}


def merge_memory_trace(meta: dict[str, Any], trace: MessageTrace) -> dict[str, Any]:
    merged = dict(meta or {})
    memory_retrieval = trace.build()
    if memory_retrieval is not None:
        merged["memory_retrieval"] = memory_retrieval
    return merged
