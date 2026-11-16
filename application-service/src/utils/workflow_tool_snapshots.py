"""Name snapshots for the tool selectors of workflow ``agent`` nodes.

A tool's display name lives only on its ``tool_configs`` row. Once that row is
gone (hard delete / cross-tenant / malformed id) a saved reference cannot
resolve a name at read time, so the name has to be captured while the row still
exists — on every workflow save.

A workflow save is a full replacement of ``nodes`` and the editor sends only
``{tool_id, operation, enabled}`` per selector, so without a stored fallback one
save would erase the name. Resolution order per selector: live row → incoming
payload → already stored config → no name at all.

A name belongs to the tool, not to one of its methods: an entry saved as a
whole-tool selection still names the tool after the reference degrades to a
single method, and the other way round.
"""

from __future__ import annotations

import uuid
from typing import Any

AGENT_NODE_TYPE = "agent"


def _agent_tools(node: Any) -> list[Any] | None:
    """The raw ``tools`` list of an ``agent`` node, or None for anything else."""
    if not isinstance(node, dict) or node.get("type") != AGENT_NODE_TYPE:
        return None
    config = node.get("config")
    if not isinstance(config, dict):
        return None
    tools = config.get("tools")
    return tools if isinstance(tools, list) else None


def _snapshots(nodes: list[Any] | None) -> dict[str, str]:
    """Names already persisted for a tool, keyed by tool_id."""
    names: dict[str, str] = {}
    for node in nodes or []:
        for selector in _agent_tools(node) or []:
            if not isinstance(selector, dict):
                continue
            tool_id = selector.get("tool_id")
            name = selector.get("name")
            if isinstance(tool_id, str) and isinstance(name, str) and name.strip():
                names.setdefault(tool_id, name.strip())
    return names


def _referenced_ids(nodes: list[Any]) -> list[uuid.UUID]:
    found: list[uuid.UUID] = []
    seen: set[str] = set()
    for node in nodes:
        for selector in _agent_tools(node) or []:
            if not isinstance(selector, dict):
                continue
            raw = selector.get("tool_id")
            if not isinstance(raw, str) or raw in seen:
                continue
            seen.add(raw)
            try:
                found.append(uuid.UUID(raw))
            except (TypeError, ValueError):
                continue
    return found


def refresh_agent_tool_names(
    db: Any,
    tenant_id: uuid.UUID | None,
    nodes: list[Any] | None,
    stored_nodes: list[Any] | None = None,
) -> list[Any]:
    """Return ``nodes`` with a name snapshot on every agent tool selector.

    ``nodes`` is never mutated: the caller's list comes straight from a request
    payload or a SQLAlchemy JSON column, and writing into the latter would not be
    tracked as a change.
    """

    prepared: list[Any] = []
    for node in nodes or []:
        tools = _agent_tools(node)
        if tools is None:
            prepared.append(node)
            continue
        config = dict(node["config"])
        config["tools"] = [
            dict(selector) if isinstance(selector, dict) else selector
            for selector in tools
        ]
        prepared.append({**node, "config": config})

    live_names = _live_names(db, tenant_id, _referenced_ids(prepared))
    stored_names = _snapshots(stored_nodes)

    for node in prepared:
        for selector in _agent_tools(node) or []:
            if not isinstance(selector, dict):
                continue
            tool_id = selector.get("tool_id")
            if not isinstance(tool_id, str):
                continue
            name = (
                live_names.get(tool_id)
                or _text(selector.get("name"))
                or stored_names.get(tool_id)
            )
            if name:
                selector["name"] = name
            else:
                selector.pop("name", None)
    return prepared


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _live_names(
    db: Any,
    tenant_id: uuid.UUID | None,
    tool_ids: list[uuid.UUID],
) -> dict[str, str]:
    """Current names of this tenant's tool rows (deleted rows included)."""

    if db is None or tenant_id is None or not tool_ids:
        return {}
    from ..repositories.tool_repository import ToolRepository

    names: dict[str, str] = {}
    for row in ToolRepository(db).find_by_ids_and_tenant_all(tool_ids, tenant_id):
        name = _text(getattr(row, "name", None))
        if name:
            names[str(row.id)] = name
    return names


__all__ = ["refresh_agent_tool_names"]