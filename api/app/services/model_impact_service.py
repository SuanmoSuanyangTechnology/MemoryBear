"""模型影响面清单（M6 §18.2）：删除/软停用前反查业务引用。

- 7 类引用各一条批量 IN 查询（无 N+1）；config_ids 为空时零查询直接返回空清单
- 只读：不改数据、不抛业务异常；同一业务实体多槽位命中合并为一条（detail 记录槽位）
- item 形如 ``{"type", "id", "name", "detail", "config_id"}``，config_id 供跨租户聚合复用
"""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.agent_app_config_model import AgentConfig
from app.models.annotation_model import AppAnnotationSetting
from app.models.app_model import App
from app.models.app_release_model import AppRelease
from app.models.knowledge_model import Knowledge
from app.models.multi_agent_model import MultiAgentConfig
from app.models.workspace_model import Workspace, WorkspaceDefaultModelPreset

WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", "vision", "audio", "video")
KNOWLEDGE_MODEL_SLOTS = {
    "embedding_id": "embedding",
    "reranker_id": "rerank",
    "llm_id": "llm",
    "image2text_id": "image2text",
}
KNOWLEDGE_STATUS_SOFT_DELETED = 2

PRESET_KEY_LABEL = "工作空间默认预设"


def _slots_by_config(row, slot_labels: dict[str, str], id_strings: set[str]) -> dict[str, list]:
    """行内多槽位按 config_id 归并：{config_id: [槽位标签...]}（行内命中顺序稳定）。"""
    grouped: dict[str, list] = {}
    for column, label in slot_labels.items():
        value = getattr(row, column)
        key = str(value)
        if key in id_strings:
            grouped.setdefault(key, []).append(label)
    return grouped


def _workspace_items(db: Session, id_strings: set[str]) -> list[dict]:
    columns = [getattr(Workspace, slot) for slot in WORKSPACE_MODEL_SLOTS]
    rows = (
        db.query(Workspace.id, Workspace.name, *columns)
        .filter(or_(*[column.in_(sorted(id_strings)) for column in columns]))
        .order_by(Workspace.name)
        .all()
    )
    items = []
    for row in rows:
        slot_labels = {slot: slot for slot in WORKSPACE_MODEL_SLOTS}
        for config_id, slots in _slots_by_config(row, slot_labels, id_strings).items():
            items.append(
                {
                    "type": "workspace",
                    "id": str(row.id),
                    "name": row.name,
                    "detail": f"占用槽位: {', '.join(slots)}",
                    "config_id": config_id,
                }
            )
    return items


def _preset_items(db: Session, config_ids: list[uuid.UUID]) -> list[dict]:
    slot_labels = {f"{slot}_model_config_id": slot for slot in WORKSPACE_MODEL_SLOTS}
    columns = [getattr(WorkspaceDefaultModelPreset, column) for column in slot_labels]
    rows = (
        db.query(WorkspaceDefaultModelPreset.id, *columns)
        .filter(or_(*[column.in_(config_ids) for column in columns]))
        .all()
    )
    id_strings = {str(value) for value in config_ids}
    items = []
    for row in rows:
        for config_id, slots in _slots_by_config(row, slot_labels, id_strings).items():
            items.append(
                {
                    "type": "workspace_default_preset",
                    "id": str(row.id),
                    "name": PRESET_KEY_LABEL,
                    "detail": f"默认预设占用槽位: {', '.join(slots)}",
                    "config_id": config_id,
                }
            )
    return items


def _app_level_items(db: Session, config_ids: list[uuid.UUID]) -> list[dict]:
    """agent_configs / multi_agent_configs / app_releases / app_annotation_settings 四类应用侧引用。"""
    items: list[dict] = []

    agent_rows = (
        db.query(AgentConfig.default_model_config_id, App.id.label("app_id"), App.name)
        .join(App, App.id == AgentConfig.app_id)
        .filter(AgentConfig.default_model_config_id.in_(config_ids))
        .order_by(App.name)
        .all()
    )
    for row in agent_rows:
        items.append(
            {
                "type": "agent_app",
                "id": str(row.app_id),
                "name": row.name,
                "detail": "Agent 应用默认模型",
                "config_id": str(row.default_model_config_id),
            }
        )

    multi_agent_rows = (
        db.query(MultiAgentConfig.default_model_config_id, App.id.label("app_id"), App.name)
        .join(App, App.id == MultiAgentConfig.app_id)
        .filter(MultiAgentConfig.default_model_config_id.in_(config_ids))
        .order_by(App.name)
        .all()
    )
    for row in multi_agent_rows:
        items.append(
            {
                "type": "multi_agent_app",
                "id": str(row.app_id),
                "name": row.name,
                "detail": "多 Agent 应用默认模型",
                "config_id": str(row.default_model_config_id),
            }
        )

    release_rows = (
        db.query(
            AppRelease.id,
            AppRelease.version_name,
            AppRelease.default_model_config_id,
            App.id.label("app_id"),
            App.name,
        )
        .join(App, App.id == AppRelease.app_id)
        .filter(
            AppRelease.default_model_config_id.in_(config_ids),
            AppRelease.is_active.is_(True),
        )
        .order_by(App.name, AppRelease.version)
        .all()
    )
    for row in release_rows:
        items.append(
            {
                "type": "app_release",
                "id": str(row.id),
                "name": row.name,
                "detail": f"发布版本 {row.version_name}",
                "config_id": str(row.default_model_config_id),
            }
        )

    annotation_rows = (
        db.query(AppAnnotationSetting.model_config_id, App.id.label("app_id"), App.name)
        .join(App, App.id == AppAnnotationSetting.app_id)
        .filter(AppAnnotationSetting.model_config_id.in_(config_ids))
        .order_by(App.name)
        .all()
    )
    for row in annotation_rows:
        items.append(
            {
                "type": "annotation_setting",
                "id": str(row.app_id),
                "name": row.name,
                "detail": "应用标注 Embedding 模型",
                "config_id": str(row.model_config_id),
            }
        )
    return items


def _knowledge_items(db: Session, config_ids: list[uuid.UUID]) -> list[dict]:
    columns = [getattr(Knowledge, column) for column in KNOWLEDGE_MODEL_SLOTS]
    rows = (
        db.query(Knowledge.id, Knowledge.name, *columns)
        .filter(
            Knowledge.status != KNOWLEDGE_STATUS_SOFT_DELETED,
            or_(*[column.in_(config_ids) for column in columns]),
        )
        .order_by(Knowledge.name)
        .all()
    )
    id_strings = {str(value) for value in config_ids}
    items = []
    for row in rows:
        for config_id, slots in _slots_by_config(row, KNOWLEDGE_MODEL_SLOTS, id_strings).items():
            items.append(
                {
                    "type": "knowledge",
                    "id": str(row.id),
                    "name": row.name,
                    "detail": f"占用槽位: {', '.join(slots)}",
                    "config_id": config_id,
                }
            )
    return items


def collect_model_impact(
    db: Session, config_ids: Iterable[uuid.UUID]
) -> dict:
    """收集模型配置的业务引用清单：``{"total": int, "items": [...]}``。

    引用面：工作空间槽位、工作空间默认预设、Agent/多 Agent 应用默认模型、
    生效发布版本、应用标注设置、知识库槽位（已软删知识库不计）。
    """
    ids = list(dict.fromkeys(config_ids))
    if not ids:
        return {"total": 0, "items": []}
    items: list[dict] = []
    items.extend(_workspace_items(db, [str(value) for value in ids]))
    items.extend(_preset_items(db, ids))
    items.extend(_app_level_items(db, ids))
    items.extend(_knowledge_items(db, ids))
    return {"total": len(items), "items": items}


__all__ = ["collect_model_impact"]
