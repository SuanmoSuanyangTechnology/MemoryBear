"""模型影响面清单（M6 §18.2）：删除/软停用前反查业务引用。

- 各类引用各一条批量查询（无 N+1）；config_ids 与 base_pairs 均为空时零查询返回空清单
- JSON 列引用（工作流节点 / Agent 知识检索 / 发布冻结快照）统一"文本粗筛 + 递归精确匹配"
- 只读：不改数据、不抛业务异常；同一业务实体多槽位命中合并为一条（detail 记录槽位）
- item 形如 ``{"type", "id", "name", "detail", "config_id"}``，config_id 供跨租户聚合复用
- 组合成员按 (provider, name) 匹配（D13：model_base 软停用面；声明即引用）
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from sqlalchemy import Text, or_
from sqlalchemy.orm import Session

from app.models.agent_app_config_model import AgentConfig
from app.models.annotation_model import AppAnnotationSetting
from app.models.app_model import App
from app.models.app_release_model import AppRelease
from app.models.knowledge_model import Knowledge
from app.models.memory_config_model import MemoryConfig
from app.models.models_model import ModelConfig
from app.models.multi_agent_model import MultiAgentConfig
from app.models.workflow_model import WorkflowConfig
from app.models.workspace_model import Workspace, WorkspaceDefaultModelPreset
from app.services.channel_registry import parse_members

WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", "vision", "audio", "video")
KNOWLEDGE_MODEL_SLOTS = {
    "embedding_id": "embedding",
    "reranker_id": "rerank",
    "llm_id": "llm",
    "image2text_id": "image2text",
}
MEMORY_CONFIG_SLOTS = {
    "llm_id": "llm",
    "embedding_id": "embedding",
    "rerank_id": "rerank",
    "vision_id": "vision",
    "audio_id": "audio",
    "video_id": "video",
    "reflection_model_id": "reflection",
    "emotion_model_id": "emotion",
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


def _memory_config_items(db: Session, id_strings: set[str]) -> list[dict]:
    columns = [getattr(MemoryConfig, column) for column in MEMORY_CONFIG_SLOTS]
    rows = (
        db.query(MemoryConfig.config_id, MemoryConfig.config_name, *columns)
        .filter(or_(*[column.in_(sorted(id_strings)) for column in columns]))
        .order_by(MemoryConfig.config_name)
        .all()
    )
    items = []
    for row in rows:
        for config_id, slots in _slots_by_config(row, MEMORY_CONFIG_SLOTS, id_strings).items():
            items.append(
                {
                    "type": "memory_config",
                    "id": str(row.config_id),
                    "name": row.config_name,
                    "detail": f"占用槽位: {', '.join(slots)}",
                    "config_id": config_id,
                }
            )
    return items


CONFIG_MODEL_REF_KEYS = frozenset({"model_id", "reranker_id"})


def _config_model_refs(config: Any) -> set[str]:
    """配置 JSON 内模型引用递归收集（工作流节点 config / Agent 知识检索 / 发布快照通用）。

    `model_id`：llm/agent/分类器/提取器顶层 + 知识库 metadata_model 嵌套；
    `reranker_id`：知识检索节点与 Agent 知识库工具（运行期经 rerank 解析拿 key）。
    """
    found: set[str] = set()
    if isinstance(config, dict):
        for key, value in config.items():
            if key in CONFIG_MODEL_REF_KEYS and isinstance(value, str) and value:
                found.add(value)
            else:
                found |= _config_model_refs(value)
    elif isinstance(config, list):
        for item in config:
            found |= _config_model_refs(item)
    return found


def _workflow_items(db: Session, id_strings: set[str]) -> list[dict]:
    """工作流节点引用：nodes JSONB 文本粗筛（单查询）→ 节点 config 模型引用键递归精确匹配。"""
    rows = (
        db.query(WorkflowConfig.id, WorkflowConfig.nodes, App.id.label("app_id"), App.name)
        .join(App, App.id == WorkflowConfig.app_id)
        .filter(
            or_(
                *[
                    WorkflowConfig.nodes.cast(Text).like(f"%{value}%")
                    for value in sorted(id_strings)
                ]
            )
        )
        .order_by(App.name)
        .all()
    )
    items = []
    for row in rows:
        hit_nodes: dict[str, list[str]] = {}
        for node in row.nodes or []:
            if not isinstance(node, dict):
                continue
            node_label = str(node.get("name") or node.get("id") or "-")
            for value in _config_model_refs(node.get("config")):
                if value in id_strings:
                    hit_nodes.setdefault(value, []).append(node_label)
        for config_id, node_labels in hit_nodes.items():
            items.append(
                {
                    "type": "workflow_node",
                    "id": str(row.app_id),
                    "name": row.name,
                    "detail": f"工作流节点: {', '.join(node_labels)}",
                    "config_id": config_id,
                }
            )
    return items


def _agent_retrieval_items(db: Session, id_strings: set[str]) -> list[dict]:
    """Agent 应用知识库检索配置引用：`agent_configs.knowledge_retrieval` JSON 内模型引用键。"""
    rows = (
        db.query(AgentConfig.app_id, AgentConfig.knowledge_retrieval, App.name)
        .join(App, App.id == AgentConfig.app_id)
        .filter(
            or_(
                *[
                    AgentConfig.knowledge_retrieval.cast(Text).like(f"%{value}%")
                    for value in sorted(id_strings)
                ]
            )
        )
        .order_by(App.name)
        .all()
    )
    items = []
    for row in rows:
        for config_id in sorted(_config_model_refs(row.knowledge_retrieval) & id_strings):
            items.append(
                {
                    "type": "agent_knowledge_retrieval",
                    "id": str(row.app_id),
                    "name": row.name,
                    "detail": "Agent 应用知识库检索配置",
                    "config_id": config_id,
                }
            )
    return items


def _release_config_items(db: Session, id_strings: set[str]) -> list[dict]:
    """生效发布版本冻结快照引用：`app_releases.config` JSON（触发/定时运行从快照重建工作流）。

    与 `default_model_config_id` 列扫描（`_app_level_items`）互补：草稿改版后快照
    仍引用旧模型，仅列扫描会漏判。
    """
    rows = (
        db.query(
            App.id,
            App.name,
            AppRelease.id.label("release_id"),
            AppRelease.version_name,
            AppRelease.config,
        )
        .join(AppRelease, AppRelease.app_id == App.id)
        .filter(
            AppRelease.is_active.is_(True),
            or_(
                *[
                    AppRelease.config.cast(Text).like(f"%{value}%")
                    for value in sorted(id_strings)
                ]
            ),
        )
        .order_by(App.name, AppRelease.version)
        .all()
    )
    items = []
    for row in rows:
        for config_id in sorted(_config_model_refs(row.config) & id_strings):
            items.append(
                {
                    "type": "app_release_config",
                    "id": str(row.release_id),
                    "name": row.name,
                    "detail": f"发布版本 {row.version_name} 冻结配置",
                    "config_id": config_id,
                }
            )
    return items


def _composite_member_items(db: Session, base_pairs: set[tuple[str, str]]) -> list[dict]:
    """组合成员声明引用 (provider, name)：config 文本粗筛 → `parse_members` 同口径精确匹配。"""
    if not base_pairs:
        return []
    names = sorted({name for _, name in base_pairs})
    rows = (
        db.query(ModelConfig.id, ModelConfig.name, ModelConfig.config)
        .filter(
            ModelConfig.is_composite.is_(True),
            or_(*[ModelConfig.config.cast(Text).like(f"%{name}%") for name in names]),
        )
        .order_by(ModelConfig.name)
        .all()
    )
    items = []
    for row in rows:
        for pair in parse_members(row.config):
            if pair in base_pairs:
                items.append(
                    {
                        "type": "composite_member",
                        "id": str(row.id),
                        "name": row.name,
                        "detail": f"组合成员: {pair[0]}/{pair[1]}",
                        "config_id": str(row.id),
                    }
                )
    return items


def collect_model_impact(
    db: Session,
    config_ids: Iterable[uuid.UUID],
    base_pairs: Iterable[tuple[str, str]] | None = None,
) -> dict:
    """收集模型配置的业务引用清单：``{"total": int, "items": [...]}``。

    引用面（config_ids）：工作空间槽位、工作空间默认预设、Agent/多 Agent 应用默认模型、
    生效发布版本（列 + 冻结快照 JSON）、应用标注设置、知识库槽位（已软删知识库不计）、
    记忆配置槽位、工作流节点、Agent 应用知识库检索配置。
    引用面（base_pairs，model_base 软停用面）：组合成员声明按 (provider, name) 匹配。
    """
    ids = list(dict.fromkeys(config_ids))
    pairs = set(base_pairs or ())
    if not ids and not pairs:
        return {"total": 0, "items": []}
    id_strings = {str(value) for value in ids}
    items: list[dict] = []
    if ids:
        items.extend(_workspace_items(db, id_strings))
        items.extend(_preset_items(db, ids))
        items.extend(_app_level_items(db, ids))
        items.extend(_knowledge_items(db, ids))
        items.extend(_memory_config_items(db, id_strings))
        items.extend(_workflow_items(db, id_strings))
        items.extend(_agent_retrieval_items(db, id_strings))
        items.extend(_release_config_items(db, id_strings))
    items.extend(_composite_member_items(db, pairs))
    return {"total": len(items), "items": items}


__all__ = ["collect_model_impact"]
