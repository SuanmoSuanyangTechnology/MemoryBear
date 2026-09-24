"""模型引用门禁（M6 §18.2；2026-09-20 收窄）：删除/软停用前反查业务引用。

- 引用面 = 已发布应用的**生效发布版本**（``app.current_release_id`` 指向的快照）+ 空间
  （槽位 / 默认预设）；草稿配置、历史发布版本、知识库、记忆配置、标注设置不计入
  （草稿引用由发布门禁 ``ModelConfigService.assert_refs_publishable`` 拦截；
  历史版本由回滚 / 工作流发布为工具两处激活点各自拦截）
- 各类引用各一条批量查询（无 N+1）；config_ids 与 base_pairs 均为空时零查询返回空清单
- JSON 列引用（发布冻结快照）统一"文本粗筛 + 递归精确匹配"
- 只读：不改数据、不抛业务异常；同一业务实体多槽位命中合并为一条（detail 记录槽位）
- item 形如 ``{"type", "id", "name", "detail", "config_id"}``，config_id 供跨租户聚合复用
- 组合成员按 (provider, name) 匹配（D13：model_base 软停用面；声明即引用）
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from sqlalchemy import Text, or_
from sqlalchemy.orm import Session

from app.models.app_model import App, AppStatus
from app.models.app_release_model import AppRelease
from app.models.models_model import ModelConfig, ModelProvider
from app.models.workspace_model import Workspace, WorkspaceDefaultModelPreset
from app.services.channel_registry import parse_members

WORKSPACE_MODEL_SLOTS = ("llm", "embedding", "rerank", "vision", "audio", "video")

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


def _release_items(db: Session, config_ids: list[uuid.UUID]) -> list[dict]:
    """生效发布版本默认模型列引用（仅已发布且未删除应用的 current_release_id 版本）。"""
    rows = (
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
            # 生效口径：仅 current_release_id 指向的版本（回滚/工具绑定在各自激活点拦截）
            App.current_release_id == AppRelease.id,
            # 已发布应用口径：status=active（发布置位、不回退）× 未删除
            App.status == AppStatus.ACTIVE,
            App.is_active.is_(True),
        )
        .order_by(App.name, AppRelease.version)
        .all()
    )
    return [
        {
            "type": "app_release",
            "id": str(row.id),
            "name": row.name,
            "detail": f"发布版本 {row.version_name}",
            "config_id": str(row.default_model_config_id),
        }
        for row in rows
    ]


CONFIG_MODEL_REF_KEYS = frozenset({"model_id", "reranker_id"})


def config_model_refs(config: Any) -> set[str]:
    """配置 JSON 内模型引用递归收集（发布冻结快照与草稿发布门禁共用）。

    `model_id`：llm/agent/分类器/提取器顶层 + 知识库 metadata_model 嵌套；
    `reranker_id`：知识检索节点与 Agent 知识库工具（运行期经 rerank 解析拿 key）。
    """
    found: set[str] = set()
    if isinstance(config, dict):
        for key, value in config.items():
            if key in CONFIG_MODEL_REF_KEYS and isinstance(value, str) and value:
                found.add(value)
            else:
                found |= config_model_refs(value)
    elif isinstance(config, list):
        for item in config:
            found |= config_model_refs(item)
    return found


def config_ref_ids(
    config: Any,
    default_model_config_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """快照模型引用 id 收集（默认模型列 + JSON 引用键）：去重保序、非法 UUID 跳过。

    发布 / 回滚 / 工作流发布为工具三处激活点门禁共用，与影响面扫描同引用口径。
    """
    ref_ids: list[uuid.UUID] = []
    if default_model_config_id is not None:
        ref_ids.append(default_model_config_id)
    for raw in config_model_refs(config):
        try:
            parsed = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            continue
        if parsed not in ref_ids:
            ref_ids.append(parsed)
    return ref_ids


def _release_config_items(db: Session, id_strings: set[str]) -> list[dict]:
    """生效发布版本冻结快照引用（触发/定时运行按 current_release_id 重建工作流）。"""
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
            # 生效口径：仅 current_release_id 指向的版本
            App.current_release_id == AppRelease.id,
            App.status == AppStatus.ACTIVE,
            App.is_active.is_(True),
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
        for config_id in sorted(config_model_refs(row.config) & id_strings):
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
            ModelConfig.provider == ModelProvider.COMPOSITE,
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

    引用面（config_ids）：工作空间槽位、工作空间默认预设、已发布应用的**生效发布版本**
    （current_release_id 指向版本的 default_model_config_id 列 + config 冻结快照 JSON，
    仅 status=active 且未删除的应用）。
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
        items.extend(_release_items(db, ids))
        items.extend(_release_config_items(db, id_strings))
    items.extend(_composite_member_items(db, pairs))
    return {"total": len(items), "items": items}


__all__ = ["collect_model_impact", "config_model_refs", "config_ref_ids"]
