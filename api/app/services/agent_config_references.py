from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.models.agent_app_config_model import AgentConfig
from app.repositories.agent_config_reference_repository import (
    available_knowledge_ids,
    available_reranker_ids,
    available_skill_ids,
    tenant_tool_ids,
)
from app.repositories.tool_repository import ToolRepository
from app.schemas.app_schema import AgentConfigCreate, AgentConfigUpdate

_uuid_adapter = TypeAdapter(UUID)


def validate_agent_config_references(
    db: Session,
    config: AgentConfigCreate | AgentConfigUpdate,
    workspace_id: UUID,
    tenant_id: UUID | None,
) -> UUID | None:
    """Check references after DTO validation and before changing any app row.

    Tool references are intentionally **not** checked here: ownership and
    active state belong to the tool-selection validator, which only judges
    ``enabled`` entries and grandfathers pairs already present in the stored
    config. Re-checking every entry here would reject a stale disabled entry on
    an unrelated save.
    """
    retrieval = config.knowledge_retrieval
    kb_ids = (
        {entry.kb_id for entry in retrieval.knowledge_bases} if retrieval else set()
    )
    reranker_id = retrieval.reranker_id if retrieval else None
    skill_ids = set(config.skills.skill_ids or []) if config.skills else set()
    if not (kb_ids or reranker_id or skill_ids):
        return tenant_id

    tenant_id = tenant_id or ToolRepository.get_tenant_id_by_workspace_id(
        db, workspace_id
    )
    if tenant_id is None:
        raise BusinessException("应用所属工作空间不存在", BizCode.NOT_FOUND)

    if kb_ids - available_knowledge_ids(db, kb_ids, workspace_id).keys():
        raise BusinessException("引用的知识库不可用", BizCode.INVALID_PARAMETER)
    if skill_ids - available_skill_ids(db, skill_ids, tenant_id):
        raise BusinessException("引用的技能不可用", BizCode.INVALID_PARAMETER)
    if reranker_id and not available_reranker_ids(db, {reranker_id}, tenant_id):
        raise BusinessException("引用的重排模型不可用", BizCode.INVALID_PARAMETER)
    return tenant_id


def _stored_tool_keys(stored_tools: Any) -> set[tuple[str, str | None]]:
    """``(tool_id, operation)`` pairs already persisted in the stored config."""
    if not isinstance(stored_tools, list):
        return set()
    keys: set[tuple[str, str | None]] = set()
    for item in stored_tools:
        if not isinstance(item, dict):
            continue
        tool_id = item.get("tool_id")
        if tool_id:
            keys.add((str(tool_id), item.get("operation") or None))
    return keys


def validate_enabled_tool_refs(
    db: Session,
    config: AgentConfigCreate | AgentConfigUpdate,
    tenant_id: UUID,
    stored_tools: list | dict | None = None,
) -> None:
    """Reject **enabled** tool references this tenant cannot resolve.

    Mirrors ``application-service``'s ``validate_tool_selection`` judging rule:
    a disabled entry is never an error, and a ``(tool_id, operation)`` pair that
    is already stored is grandfathered so a stale selection cannot block an
    unrelated save. Method-level checks stay in application-service; the
    monolith only enforces ownership and active state.
    """
    entries = [
        entry for entry in (config.tools or []) if entry.enabled and entry.tool_id
    ]
    if not entries:
        return
    stored = _stored_tool_keys(stored_tools)
    pending = [
        entry
        for entry in entries
        if (str(entry.tool_id), entry.operation or None) not in stored
    ]
    if not pending:
        return
    available = tenant_tool_ids(db, {entry.tool_id for entry in pending}, tenant_id, active_only=True)
    for entry in pending:
        if entry.tool_id not in available:
            detail = {"tool_id": str(entry.tool_id), "operation": entry.operation}
            raise BusinessException(
                "工具已停用或删除", BizCode.TOOL_SELECTION_INACTIVE, context=detail
            )


def parse_reference_id(value: Any) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return _uuid_adapter.validate_python(value)
    except ValidationError:
        return None


def _entries(value: Any) -> list[dict]:
    return (
        [entry for entry in value if isinstance(entry, dict)]
        if isinstance(value, list)
        else []
    )


def filter_copied_agent_config(
    db: Session,
    source: AgentConfig,
    source_workspace_id: UUID,
    target_workspace_id: UUID,
    target_tenant_id: UUID,
) -> tuple[dict | None, list | dict, dict]:
    """Copy historical JSON with only references usable in the destination."""
    retrieval = deepcopy(source.knowledge_retrieval)
    if isinstance(retrieval, dict):
        bases = [
            (entry, parse_reference_id(entry.get("kb_id")))
            for entry in _entries(retrieval.get("knowledge_bases"))
        ]
        ids = {kb_id for _, kb_id in bases if kb_id is not None}
        mapped = available_knowledge_ids(
            db, ids, target_workspace_id, source_workspace_id
        )
        if "knowledge_bases" in retrieval:
            retrieval["knowledge_bases"] = [
                {**entry, "kb_id": str(mapped[kb_id])}
                for entry, kb_id in bases
                if kb_id in mapped
            ]
        if "reranker_id" in retrieval:
            model_id = parse_reference_id(retrieval["reranker_id"])
            available = model_id and available_reranker_ids(
                db, {model_id}, target_tenant_id
            )
            retrieval["reranker_id"] = str(model_id) if available else None
    else:
        retrieval = None

    tools = deepcopy(source.tools)
    if isinstance(tools, dict):
        # Legacy builtin selections are keyed by tool name, with no tool_id field.
        tools = {key: entry for key, entry in tools.items() if isinstance(entry, dict)}
    else:
        entries = [
            (entry, parse_reference_id(entry.get("tool_id")))
            for entry in _entries(tools)
        ]
        ids = {tool_id for _, tool_id in entries if tool_id is not None}
        available = tenant_tool_ids(db, ids, target_tenant_id, active_only=True)
        tools = [
            {**entry, "tool_id": str(tool_id)} if tool_id else entry
            for entry, tool_id in entries
            if tool_id in available
            or (entry.get("tool_id") is None and not entry.get("enabled"))
        ]

    skills = deepcopy(source.skills) if isinstance(source.skills, dict) else {}
    if isinstance(skills.get("skill_ids"), list):
        ids = [parse_reference_id(value) for value in skills["skill_ids"]]
        available = available_skill_ids(
            db, {value for value in ids if value}, target_tenant_id
        )
        skills["skill_ids"] = [str(value) for value in ids if value in available]
    elif skills.get("skill_ids") is not None:
        skills["skill_ids"] = []

    return retrieval, tools, skills
