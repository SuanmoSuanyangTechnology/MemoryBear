import asyncio
from typing import Any

_state: dict[str, dict[str, Any]] = {}


def register_intervention(execution_id: str, node_interrupt_map: dict[str, str]):
    if execution_id in _state:
        _state[execution_id]["node_map"].update(node_interrupt_map)
    else:
        _state[execution_id] = {
            "queue": asyncio.Queue(),
            "node_map": dict(node_interrupt_map),
            "pending_nodes": set(),
        }


def register_pending(execution_id: str, node_ids: set[str]):
    entry = _state.get(execution_id)
    if entry:
        entry["pending_nodes"].update(node_ids)
        for nid in node_ids:
            entry["node_map"].pop(nid, None)


def signal_timeout(execution_id: str, node_id: str | None = None) -> tuple[str, str | None] | None:
    """Push a timeout signal into the SSE queue so run_stream can resume the workflow.

    Returns (node_id, interrupt_id) if an SSE stream was active and the signal
    was delivered, None if no queue exists (no stream listening).
    """
    entry = _state.get(execution_id)
    if not entry:
        return None

    if node_id:
        if node_id in entry["node_map"]:
            interrupt_id = entry["node_map"][node_id]
            entry["queue"].put_nowait((node_id, "__timeout__", None, "timeout"))
            return (node_id, interrupt_id)
        if node_id in entry.get("pending_nodes", set()):
            entry["queue"].put_nowait((node_id, "__timeout__", None, "timeout"))
            return (node_id, None)
        return None

    # Find the first node in node_map with a real interrupt_id
    for nid, iid in entry["node_map"].items():
        entry["queue"].put_nowait((nid, "__timeout__", None, "timeout"))
        return (nid, iid)
    # Fallback: no interrupt map, push placeholder
    entry["queue"].put_nowait(("__timeout__", "__timeout__", None, "timeout"))
    return ("__timeout__", None)


def submit_intervention(execution_id: str, node_id: str, action_id: str, form_data: dict | None = None) -> str | None:
    entry = _state.get(execution_id)
    if not entry:
        return None
    if node_id in entry.get("pending_nodes", set()):
        entry["queue"].put_nowait((node_id, action_id, form_data, "pending"))
        return "pending"
    if node_id in entry["node_map"]:
        entry["queue"].put_nowait((node_id, action_id, form_data, "interrupt"))
        return "interrupt"
    return None


async def wait_for_next(execution_id: str) -> tuple[str | None, str | None, dict | None, str | None, str | None]:
    entry = _state.get(execution_id)
    if not entry:
        return None, None, None, None, None
    node_id, action_id, form_data, kind = await entry["queue"].get()
    interrupt_id = entry["node_map"].get(node_id) if kind in ("interrupt", "timeout") else None
    entry.get("pending_nodes", set()).discard(node_id)
    entry["node_map"].pop(node_id, None)
    return node_id, action_id, form_data, interrupt_id, kind


def cleanup_intervention(execution_id: str):
    _state.pop(execution_id, None)
