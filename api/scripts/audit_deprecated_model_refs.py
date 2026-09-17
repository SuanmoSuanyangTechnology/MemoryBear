"""下线模型影响面审计（阿里云 2026-10-10 批次）：只读输出按租户分层的引用清单。

背景：业务配置只存 `model_configs.id`（UUID），模型名只落在少数几张表。本脚本按
下线名单做三层扫描，输出「租户 → 空间 → (知识库, 记忆) → 应用」分层 CSV，供运营
逐租户通知迁移：

1. 名称直匹配：model_configs.name、组合模型 config.members[].model_name、
   model_channels.model_names、旧表 model_api_keys.model_name（经 association 归租户，
   且其关联 config 一并纳入反查——运行期真实调用名以密钥的 model_name 为准）；
2. 由命中 config 反查业务引用：workspaces / knowledges / memory_config /
   agent_configs / multi_agent_configs / app_annotation_settings /
   workflow_configs.nodes（草稿）/ app_releases（含 config.nodes）/
   workspace_default_model_presets（全局单例）；
3. 裸名扫描：workflow 节点 JSON 内 `model` / `metadata_model.model` 字符串命中名单，
   但同层无 model_id（运行期不可用，仅提示运营）。

- 只读：不写库，结束 rollback；批量 IN 查询（无 N+1）；输出不含密钥与联系人信息。
- 模型名精确匹配（大小写敏感）；UUID 槽位按 UUID 语义归一化（大小写不敏感）。
- model_configs.name 直命中仅作反查入口（无业务引用的配置不出现在清单中，仅摘要提示）；
  组合成员会单列，因为它是组合模型的组成部分。
- 名称不在 dashscope_models.yaml 的模型无法用 yaml 标记，摘要中单列提示人工处理。

用法（连接读 env，自动加载 enterprise 根 .env 其次 core/api/.env）：
    python core/api/scripts/audit_deprecated_model_refs.py
    python core/api/scripts/audit_deprecated_model_refs.py --csv /tmp/audit.csv
    python core/api/scripts/audit_deprecated_model_refs.py --models-file names.txt
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # core/api

_REPO_CANDIDATES = [
    Path(__file__).resolve().parents[3] / ".env",   # MemoryBear-Enterprise 根
    Path(__file__).resolve().parents[1] / ".env",   # core/api
]
_YAML_PATH = Path(__file__).resolve().parents[1] / "app/core/models/scripts/dashscope_models.yaml"

BATCH_LABEL = "阿里云 2026-10-10 下线批次"

# 下线名单（85 个 = 公告 73 个 + 用户对照官网复核补充 12 个；
# 按类别分组，注释为 dashscope_models.yaml 可标记情况）
DEPRECATED_MODELS = [
    # qwen 语音系列快照（5 个，均不在 yaml）
    "qwen-tts-latest",
    "qwen-tts-2025-05-22",
    "qwen-tts-2025-04-10",
    "qwen-tts-realtime-latest",
    "qwen-tts-realtime-2025-07-15",
    # 部分 qwen 与三方模型（42 个，其中 27 个在 yaml，已标 is_deprecated）
    "deepseek-v3.2",
    "deepseek-r1",
    "deepseek-v3",
    "deepseek-v3.1",
    "glm-4.7",
    "glm-4.6",
    "deepseek-v3.2-exp",
    "deepseek-r1-0528",
    "Moonshot-Kimi-K2-Instruct",
    "kimi-k2-thinking",
    "deepseek-r1-distill-qwen-32b",
    "deepseek-r1-distill-qwen-14b",
    "MiniMax-M2.1",
    "deepseek-r1-distill-qwen-7b",
    "qwen3-max-2026-01-23",
    "qwen3-max-2025-09-23",
    "qwen3-vl-flash-2026-01-22",
    "qwen3-vl-flash-2025-10-15",
    "qwen3-coder-plus-2025-09-23",
    "qwen3-coder-plus-2025-07-22",
    "qwen3-235b-a22b-instruct-2507",
    "qwen3-32b",
    "qwen3-vl-235b-a22b-instruct",
    "qwen3-vl-32b-thinking",
    "qwen3-vl-32b-instruct",
    "qwen3-vl-30b-a3b-thinking",
    "qwen3-vl-30b-a3b-instruct",
    "qwen3-vl-8b-thinking",
    "qwen3-vl-8b-instruct",
    "qwen3-vl-235b-a22b-thinking",
    "qwen3-next-80b-a3b-instruct",
    "qwen3-next-80b-a3b-thinking",
    "qwen3-30b-a3b-instruct-2507",
    "qwen3-30b-a3b-thinking-2507",
    "qwen3-235b-a22b-thinking-2507",
    "qwen3-235b-a22b",
    "qwen3-30b-a3b",
    "qwen3-14b",
    "qwen3-8b",
    "qwen3-coder-next",
    "qwen3-coder-30b-a3b-instruct",
    "qwen3-coder-480b-a35b-instruct",
    # qwen 系列老旧主线（10 个，其中 4 个在 yaml，已标 is_deprecated）
    "qwen-turbo",
    "qwen-turbo-realtime",
    "qwen-vl-max",
    "qwen-vl-plus",
    "qwq-plus",
    "qvq-max",
    "qvq-plus",
    "qwen-math-turbo",
    "qwen-coder-turbo",
    "qwen-coder-plus",
    # 语音系列主线（11 个，均不在 yaml）
    "qwen-tts",
    "qwen-tts-realtime",
    "qwen-voice-enrollment",
    "qwen-voice-design",
    "gummy-chat-v1",
    "gummy-realtime-v1",
    "paraformer-realtime-v1",
    "paraformer-realtime-8k-v1",
    "paraformer-v1",
    "paraformer-8k-v1",
    "paraformer-mtl-v1",
    # qwen3 系列主线（5 个，其中 4 个在 yaml，已标 is_deprecated）
    "qwen3.6-max-preview",
    "qwen3-max-preview",
    "qwen3-max",
    "qwen3-vl-flash",
    "qwen3-coder-plus",
    # 官网复核补充（12 个，2026-09-16 用户对照阿里云官网核对后标记；均在 yaml，已标 is_deprecated）
    "qwen-max-latest",
    "qwen-vl-plus-2025-01-02",
    "qwen-vl-plus-2025-01-25",
    "qwen-vl-plus-latest",
    "qwen3-vl-plus-2025-09-23",
    "qwen3-omni-flash-2025-12-01",
    "qwen2.5-0.5b-instruct",
    "qwen3-4b",
    "qvq-max-latest",
    "qwq-32b",
    "qwq-plus-0305",
    "deepseek-v4-flash",
]

_ID_REF_KEYS = ("model_id", "model_config_id", "default_model_config_id", "reranker_id")
_NAME_REF_KEYS = ("model", "metadata_model")

# 槽位口径与 app/services/model_impact_service.py 的 *_SLOTS 常量保持一致
# （运行期删除前阻断依赖该服务，两处需同改）
WORKSPACE_SLOTS = ("llm", "embedding", "rerank", "vision", "audio", "video")
KNOWLEDGE_SLOTS = {
    "embedding_id": "embedding",
    "reranker_id": "rerank",
    "llm_id": "llm",
    "image2text_id": "image2text",
}
MEMORY_SLOTS = {
    "llm_id": "llm",
    "embedding_id": "embedding",
    "rerank_id": "rerank",
    "vision_id": "vision",
    "audio_id": "audio",
    "video_id": "video",
    "reflection_model_id": "reflection",
    "emotion_model_id": "emotion",
}
PRESET_SLOTS = {f"{slot}_model_config_id": slot for slot in WORKSPACE_SLOTS}
KNOWLEDGE_STATUS_SOFT_DELETED = 2

CSV_HEADERS = [
    "层级", "租户ID", "租户名", "空间ID", "空间名", "对象ID", "对象名",
    "对象类型", "引用位置", "模型名", "model_config_id", "备注",
]
_LEVEL_RANK = {"租户级": 0, "空间": 1, "知识库": 2, "记忆": 3, "应用": 4, "全局": 5}

_BARE_REMARK = "未绑定模型（无 model_id，运行期不可用）"


def _entry(
    level: str,
    object_type: str,
    object_id: str,
    position: str,
    model_names: list[str],
    *,
    object_name: str | None = None,
    app_id: str | None = None,
    space_id: str | None = None,
    space_name: str | None = None,
    tenant_id: str | None = None,
    version_name: str | None = None,
    config_id: str | None = None,
    remark: str = "",
) -> dict:
    return {
        "level": level,
        "object_type": object_type,
        "object_id": object_id,
        "object_name": object_name,
        "app_id": app_id,
        "space_id": space_id,
        "space_name": space_name,
        "tenant_id": tenant_id,
        "tenant_name": None,
        "version_name": version_name,
        "config_id": config_id,
        "model_names": list(model_names),
        "position": position,
        "remark": remark,
    }


def _load_env() -> None:
    try:
        import dotenv
    except ImportError:
        return
    for env_path in _REPO_CANDIDATES:
        if env_path.is_file():
            dotenv.load_dotenv(env_path)


def _die(message: str) -> None:
    """[err] 输出到 stderr 并退出码 2（与 core/api/scripts 既有脚本约定一致）。"""
    print(f"[err] {message}", file=sys.stderr)
    raise SystemExit(2)


def _load_names(models_file: str | None) -> list[str]:
    if not models_file:
        return list(DEPRECATED_MODELS)
    path = Path(models_file)
    if not path.is_file():
        _die(f"--models-file 不存在: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _die(f"--models-file 读取失败: {path}: {exc}")
    names = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not names:
        _die(f"--models-file 无有效模型名: {path}")
    return names


def _load_yaml_marks() -> dict[str, bool] | None:
    """读取 dashscope_models.yaml：{模型名: is_deprecated}；缺失/解析失败返回 None。"""
    try:
        import yaml
    except ImportError:
        return None
    if not _YAML_PATH.is_file():
        return None
    try:
        data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    marks: dict[str, bool] = {}
    for item in (data or {}).get("models") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            marks[item["name"]] = bool(item.get("is_deprecated"))
    return marks


def _collect_id_refs(payload, out: dict[str, list[str]]) -> None:
    """递归收集 payload 中 _ID_REF_KEYS 的取值（{key: [值...]}，保序去重）。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _ID_REF_KEYS and isinstance(value, str) and value:
                out.setdefault(key, [])
                if value not in out[key]:
                    out[key].append(value)
            else:
                _collect_id_refs(value, out)
    elif isinstance(payload, list):
        for item in payload:
            _collect_id_refs(item, out)


def _collect_bare_names(payload, names: set[str], out: list[tuple[str, str]]) -> None:
    """收集 `model` / `metadata_model` 内命中名单且无绑定 model_id 的裸名。

    兼容两种形态：`{"model": {"model_id": ..., "model": "qwen3-max"}}` 与
    `{"model": "qwen3-max"}`；同层存在 model_id/model_config_id 视为已绑定，不报。
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if (
                key in _NAME_REF_KEYS
                and isinstance(value, str)
                and value in names
                and not payload.get("model_id")
                and not payload.get("model_config_id")
            ):
                out.append((value, key))
            else:
                _collect_bare_names(value, names, out)
    elif isinstance(payload, list):
        for item in payload:
            _collect_bare_names(item, names, out)


def _scan_nodes(nodes, names: set[str]) -> list[tuple[str, str, dict, list]]:
    """节点列表 → [(node_id, node_type, id_refs, bare_names)]（有落点的节点）。"""
    hits = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        id_refs: dict[str, list[str]] = {}
        _collect_id_refs(node, id_refs)
        bare: list[tuple[str, str]] = []
        _collect_bare_names(node, names, bare)
        if id_refs or bare:
            hits.append((
                str(node.get("id") or "-"),
                str(node.get("type") or node.get("node_type") or "-"),
                id_refs,
                bare,
            ))
    return hits


def _scan_model_configs(db, names: set[str]) -> tuple[dict[str, dict], list[dict]]:
    """名称直匹配 model_configs（含组合成员）：返回 (命中配置, 组合成员条目)。

    命中配置 {config_id(str): {"tenant_id", "name", "names": set[str]}} 作为反查入口，
    仅供 _scan_business_refs 使用，不单独出行（无业务引用的配置无迁移影响）。
    """
    from app.models.models_model import ModelConfig, ModelProvider

    hits: dict[str, dict] = {}
    for row in (
        db.query(ModelConfig.id, ModelConfig.tenant_id, ModelConfig.name)
        .filter(ModelConfig.name.in_(sorted(names)))
        .all()
    ):
        key = str(row.id)
        tenant_id = str(row.tenant_id) if row.tenant_id else None
        hits.setdefault(key, {"tenant_id": tenant_id, "name": row.name, "names": set()})
        hits[key]["names"].add(row.name)

    entries: list[dict] = []
    for row in (
        db.query(ModelConfig.id, ModelConfig.tenant_id, ModelConfig.name, ModelConfig.config)
        .filter(ModelConfig.provider == ModelProvider.COMPOSITE)
        .all()
    ):
        raw = (row.config or {}).get("members") if isinstance(row.config, dict) else None
        if not isinstance(raw, list):
            continue
        matched = sorted({
            item.get("model_name")
            for item in raw
            if isinstance(item, dict) and item.get("model_name") in names
        })
        if not matched:
            continue
        key = str(row.id)
        tenant_id = str(row.tenant_id) if row.tenant_id else None
        hits.setdefault(key, {"tenant_id": tenant_id, "name": row.name, "names": set()})
        hits[key]["names"].update(matched)
        entries.append(_entry(
            "租户级", "composite_member", key, "组合成员 model_name", matched,
            object_name=row.name, tenant_id=tenant_id, config_id=key,
            remark="租户级；组合模型含下线成员",
        ))
    return hits, entries


def _scan_channels(db, names: set[str]) -> list[dict]:
    """渠道点名集合命中（[] 为 provider 级默认渠道，无具体名，不产出）。"""
    from app.models.models_model import ModelChannel

    entries: list[dict] = []
    for row in db.query(
        ModelChannel.id, ModelChannel.tenant_id, ModelChannel.provider,
        ModelChannel.model_names, ModelChannel.is_active,
    ).all():
        raw = row.model_names if isinstance(row.model_names, list) else []
        matched = sorted({name for name in raw if isinstance(name, str) and name in names})
        if not matched:
            continue
        entries.append(_entry(
            "租户级", "channel", str(row.id), "渠道模型名集合 model_names", matched,
            object_name=f"{row.provider} 渠道",
            tenant_id=str(row.tenant_id) if row.tenant_id else None,
            remark="租户级" if row.is_active else "租户级；渠道已停用",
        ))
    return entries


def _scan_api_keys(db, names: set[str]) -> tuple[list[dict], dict[str, set[str]]]:
    """旧表 model_api_keys.model_name 命中；经 association 归租户（可能多配置）。

    返回 (条目, 关联配置补充名)：后者的 {config_id: 命中名} 由调用方并入反查集合——
    config 自身名字可能不在名单，但运行期真实调用名以密钥 model_name 为准。
    """
    from app.models.models_model import (
        ModelApiKey,
        ModelConfig,
        model_config_api_key_association as assoc,
    )

    key_rows = (
        db.query(
            ModelApiKey.id, ModelApiKey.model_name, ModelApiKey.provider,
            ModelApiKey.description, ModelApiKey.is_active,
        )
        .filter(ModelApiKey.model_name.in_(sorted(names)))
        .all()
    )
    if not key_rows:
        return [], {}

    by_key: dict = {}
    for api_key_id, config_id, tenant_id in (
        db.query(assoc.c.api_key_id, ModelConfig.id, ModelConfig.tenant_id)
        .select_from(assoc)
        .join(ModelConfig, ModelConfig.id == assoc.c.model_config_id)
        .filter(assoc.c.api_key_id.in_([row.id for row in key_rows]))
        .all()
    ):
        by_key.setdefault(api_key_id, []).append({
            "config_id": str(config_id),
            "tenant_id": str(tenant_id) if tenant_id else None,
        })

    entries: list[dict] = []
    linked_config_names: dict[str, set[str]] = {}
    for row in key_rows:
        base_remark = "租户级；旧表 model_api_keys" + ("" if row.is_active else "；已停用")
        linked = by_key.get(row.id) or []
        if not linked:
            entries.append(_entry(
                "租户级", "api_key", str(row.id), "运行期调用名 model_name",
                [row.model_name], object_name=row.description or f"{row.provider} 密钥",
                remark=base_remark + "；未关联模型配置，无法归租户",
            ))
            continue
        for item in linked:
            linked_config_names.setdefault(item["config_id"], set()).add(row.model_name)
            entries.append(_entry(
                "租户级", "api_key", str(row.id), "运行期调用名 model_name",
                [row.model_name], object_name=row.description or f"{row.provider} 密钥",
                tenant_id=item["tenant_id"], config_id=item["config_id"],
                remark=base_remark,
            ))
    return entries, linked_config_names


def _match_key(value, names_by_config: dict[str, list[str]]) -> str | None:
    """槽位取值 → 规范化 config_id 键；大小写差异按 UUID 语义归一化。"""
    if value is None:
        return None
    key = str(value)
    if key in names_by_config:
        return key
    try:
        key = str(uuid.UUID(key))
    except (ValueError, AttributeError, TypeError):
        return None
    return key if key in names_by_config else None


def _node_entries(
    nodes,
    dep_names: set[str],
    names_by_config: dict[str, list[str]],
    *,
    object_id: str,
    object_type: str,
    position_prefix: str,
    app_id: str | None = None,
    version_name: str | None = None,
    remark: str = "",
) -> tuple[list[dict], list[dict]]:
    """工作流节点列表 → (引用条目, 裸名条目)；草稿与发布快照共用。"""
    refs: list[dict] = []
    bare: list[dict] = []
    for node_id, node_type, id_refs, bare_names in _scan_nodes(nodes, dep_names):
        for ref_key, values in id_refs.items():
            for value in values:
                key = _match_key(value, names_by_config)
                if key:
                    refs.append(_entry(
                        "应用", object_type, object_id,
                        f"{position_prefix} {node_type}/{node_id} ({ref_key})",
                        names_by_config[key], app_id=app_id, version_name=version_name,
                        config_id=key, remark=remark,
                    ))
        for raw_name, label in bare_names:
            bare.append(_entry(
                "应用", object_type, object_id,
                f"{position_prefix} {node_type}/{node_id} ({label})", [raw_name],
                app_id=app_id, version_name=version_name,
                remark=f"{_BARE_REMARK}；{remark}" if remark else _BARE_REMARK,
            ))
    return refs, bare


def _scan_business_refs(
    db, names_by_config: dict[str, list[str]], dep_names: set[str]
) -> tuple[list[dict], list[dict]]:
    """由命中 config 反查业务引用面：返回 (引用条目, 裸名条目)。

    每张表一条批量 IN 查询（无 N+1）；UUID 列直接 IN，String 槽位列 lower() 归一化。
    dep_names 为完整名单：裸名扫描不依赖库内是否存在同名 config。
    """
    from sqlalchemy import func, or_

    from app.models.agent_app_config_model import AgentConfig
    from app.models.annotation_model import AppAnnotationSetting
    from app.models.app_release_model import AppRelease
    from app.models.knowledge_model import Knowledge
    from app.models.memory_config_model import MemoryConfig
    from app.models.multi_agent_model import MultiAgentConfig
    from app.models.workspace_model import Workspace, WorkspaceDefaultModelPreset
    from app.models.workflow_model import WorkflowConfig

    str_ids = sorted(names_by_config)
    if not str_ids:
        return [], []
    config_ids = [uuid.UUID(key) for key in str_ids]

    def matched(value) -> str | None:
        return _match_key(value, names_by_config)

    refs: list[dict] = []
    bare: list[dict] = []

    # 空间槽位（String 列）
    workspace_columns = [getattr(Workspace, slot) for slot in WORKSPACE_SLOTS]
    for row in (
        db.query(Workspace.id, Workspace.name, Workspace.tenant_id, *workspace_columns)
        .filter(or_(*[func.lower(column).in_(str_ids) for column in workspace_columns]))
        .all()
    ):
        for slot in WORKSPACE_SLOTS:
            key = matched(getattr(row, slot))
            if key:
                refs.append(_entry(
                    "空间", "space", str(row.id), f"槽位 {slot}", names_by_config[key],
                    object_name=row.name, space_id=str(row.id), space_name=row.name,
                    tenant_id=str(row.tenant_id) if row.tenant_id else None, config_id=key,
                ))

    # 知识库槽位（软删不计）
    knowledge_columns = [getattr(Knowledge, column) for column in KNOWLEDGE_SLOTS]
    for row in (
        db.query(Knowledge.id, Knowledge.name, Knowledge.workspace_id, *knowledge_columns)
        .filter(
            Knowledge.status != KNOWLEDGE_STATUS_SOFT_DELETED,
            or_(*[column.in_(config_ids) for column in knowledge_columns]),
        )
        .all()
    ):
        for column_name, label in KNOWLEDGE_SLOTS.items():
            key = matched(getattr(row, column_name))
            if key:
                refs.append(_entry(
                    "知识库", "knowledge", str(row.id), f"槽位 {label}",
                    names_by_config[key], object_name=row.name,
                    space_id=str(row.workspace_id) if row.workspace_id else None,
                    config_id=key,
                ))

    # 记忆配置槽位（workspace 可空）
    memory_columns = [getattr(MemoryConfig, column) for column in MEMORY_SLOTS]
    for row in (
        db.query(
            MemoryConfig.config_id, MemoryConfig.config_name,
            MemoryConfig.workspace_id, *memory_columns,
        )
        .filter(or_(*[func.lower(column).in_(str_ids) for column in memory_columns]))
        .all()
    ):
        space_id = str(row.workspace_id) if row.workspace_id else None
        for column_name, label in MEMORY_SLOTS.items():
            key = matched(getattr(row, column_name))
            if key:
                refs.append(_entry(
                    "记忆", "memory_config", str(row.config_id), f"槽位 {label}",
                    names_by_config[key], object_name=row.config_name, space_id=space_id,
                    config_id=key, remark="" if space_id else "无空间归属",
                ))

    # 应用侧：Agent 默认模型 + 知识库检索重排序
    for app_id, default_id, retrieval in db.query(
        AgentConfig.app_id, AgentConfig.default_model_config_id,
        AgentConfig.knowledge_retrieval,
    ).all():
        key = matched(default_id)
        if key:
            refs.append(_entry(
                "应用", "agent", str(app_id), "Agent 默认模型", names_by_config[key],
                app_id=str(app_id), config_id=key,
            ))
        if isinstance(retrieval, dict):
            key = matched(retrieval.get("reranker_id"))
            if key:
                refs.append(_entry(
                    "应用", "agent", str(app_id), "知识库检索重排序模型",
                    names_by_config[key], app_id=str(app_id), config_id=key,
                ))

    # 多 Agent 默认模型
    for app_id, default_id in (
        db.query(MultiAgentConfig.app_id, MultiAgentConfig.default_model_config_id)
        .filter(MultiAgentConfig.default_model_config_id.in_(config_ids))
        .all()
    ):
        key = matched(default_id)
        if key:
            refs.append(_entry(
                "应用", "multi_agent", str(app_id), "多 Agent 默认模型",
                names_by_config[key], app_id=str(app_id), config_id=key,
            ))

    # 应用标注设置
    for app_id, config_id_value in (
        db.query(AppAnnotationSetting.app_id, AppAnnotationSetting.model_config_id)
        .filter(AppAnnotationSetting.model_config_id.in_(config_ids))
        .all()
    ):
        key = matched(config_id_value)
        if key:
            refs.append(_entry(
                "应用", "annotation_setting", str(app_id), "应用标注 Embedding 模型",
                names_by_config[key], app_id=str(app_id), config_id=key,
            ))

    # 工作流草稿节点
    for app_id, nodes in (
        db.query(WorkflowConfig.app_id, WorkflowConfig.nodes).yield_per(500)
    ):
        node_refs, node_bare = _node_entries(
            nodes, dep_names, names_by_config,
            object_id=str(app_id), object_type="workflow",
            position_prefix="节点", app_id=str(app_id), remark="工作流草稿",
        )
        refs += node_refs
        bare += node_bare

    # 发布版本：直接字段 + config.nodes 快照（流式，含未生效版本）
    for row in db.query(
        AppRelease.id, AppRelease.app_id, AppRelease.version_name, AppRelease.is_active,
        AppRelease.default_model_config_id, AppRelease.config,
    ).yield_per(500):
        release_remark = "生效发布版本" if row.is_active else "未生效发布版本"
        key = matched(row.default_model_config_id)
        if key:
            refs.append(_entry(
                "应用", "app_release", str(row.id), "发布版本默认模型",
                names_by_config[key], app_id=str(row.app_id),
                version_name=row.version_name, config_id=key, remark=release_remark,
            ))
        nodes = (row.config or {}).get("nodes") if isinstance(row.config, dict) else None
        node_refs, node_bare = _node_entries(
            nodes, dep_names, names_by_config,
            object_id=str(row.id), object_type="app_release",
            position_prefix="发布版本节点", app_id=str(row.app_id),
            version_name=row.version_name, remark=release_remark,
        )
        refs += node_refs
        bare += node_bare

    # 全局默认预设（单例，无租户）
    preset_columns = [getattr(WorkspaceDefaultModelPreset, column) for column in PRESET_SLOTS]
    for row in (
        db.query(WorkspaceDefaultModelPreset.id, *preset_columns)
        .filter(or_(*[column.in_(config_ids) for column in preset_columns]))
        .all()
    ):
        for column_name, label in PRESET_SLOTS.items():
            key = matched(getattr(row, column_name))
            if key:
                refs.append(_entry(
                    "全局", "preset", str(row.id), f"默认预设槽位 {label}",
                    names_by_config[key], object_name="工作空间默认预设",
                    config_id=key, remark="全局单例",
                ))

    return refs, bare


def _resolve_meta(db, entries: list[dict]) -> None:
    """批量补齐对象名/空间名/租户名（每表一条 IN 查询，无 N+1；不读联系人字段）。"""
    from app.models.app_model import App
    from app.models.workspace_model import Workspace

    app_ids = sorted({uuid.UUID(e["app_id"]) for e in entries if e.get("app_id")})
    app_meta: dict[str, tuple] = {}
    if app_ids:
        for app_id, app_name, space_id, space_name, tenant_id in (
            db.query(App.id, App.name, App.workspace_id, Workspace.name, Workspace.tenant_id)
            .join(Workspace, Workspace.id == App.workspace_id)
            .filter(App.id.in_(app_ids))
            .all()
        ):
            app_meta[str(app_id)] = (app_name, str(space_id), space_name, str(tenant_id))

    for entry in entries:
        meta = app_meta.get(entry["app_id"]) if entry.get("app_id") else None
        if not meta:
            continue
        app_name, space_id, space_name, tenant_id = meta
        if not entry["object_name"]:
            entry["object_name"] = (
                f"{app_name}@{entry['version_name']}" if entry.get("version_name") else app_name
            )
        entry["space_id"] = entry["space_id"] or space_id
        entry["space_name"] = entry["space_name"] or space_name
        entry["tenant_id"] = entry["tenant_id"] or tenant_id

    space_ids = sorted({
        uuid.UUID(e["space_id"])
        for e in entries
        if e.get("space_id") and not e.get("space_name")
    })
    space_meta: dict[str, tuple] = {}
    if space_ids:
        for space_id, space_name, tenant_id in (
            db.query(Workspace.id, Workspace.name, Workspace.tenant_id)
            .filter(Workspace.id.in_(space_ids))
            .all()
        ):
            space_meta[str(space_id)] = (space_name, str(tenant_id) if tenant_id else None)
    for entry in entries:
        if entry.get("space_id") and not entry.get("space_name"):
            meta = space_meta.get(entry["space_id"])
            if meta:
                entry["space_name"], tenant_id = meta
                entry["tenant_id"] = entry["tenant_id"] or tenant_id

    tenant_meta = _tenant_names(
        db, {e["tenant_id"] for e in entries if e.get("tenant_id")}
    )
    for entry in entries:
        if entry.get("tenant_id"):
            entry["tenant_name"] = tenant_meta.get(entry["tenant_id"])


def _tenant_names(db, tenant_ids: set[str]) -> dict[str, str]:
    """批量解析租户名（单条 IN 查询；只取 id/name，不读联系人字段）。"""
    from app.models.tenant_model import Tenants

    ids = sorted({uuid.UUID(item) for item in tenant_ids if item})
    if not ids:
        return {}
    return {
        str(tenant_id): tenant_name
        for tenant_id, tenant_name in (
            db.query(Tenants.id, Tenants.name).filter(Tenants.id.in_(ids)).all()
        )
    }


def _merge_rows(entries: list[dict]) -> list[dict]:
    """同一 (租户, 空间, 层级, 对象, config, 备注) 合并：引用位置保序去重，模型名排序去重。"""
    merged: dict[tuple, dict] = {}
    for entry in entries:
        key = (
            entry.get("tenant_id") or "",
            entry.get("space_id") or "",
            entry["level"],
            entry["object_type"],
            entry["object_id"],
            entry.get("config_id") or "",
            entry.get("remark") or "",
        )
        row = merged.get(key)
        if row is None:
            row = dict(entry)
            row["positions"] = [entry["position"]]
            merged[key] = row
        else:
            row["positions"].append(entry["position"])
        for name in entry["model_names"]:
            if name not in row["model_names"]:
                row["model_names"].append(name)

    rows = list(merged.values())
    for row in rows:
        row["positions"] = list(dict.fromkeys(row["positions"]))
        row["model_names"] = sorted(row["model_names"])
    rows.sort(key=lambda row: (
        row["level"] == "全局",  # 全局单例置尾
        row.get("tenant_name") or "",
        row.get("space_name") or "",
        _LEVEL_RANK.get(row["level"], 9),
        row.get("object_name") or "",
        ",".join(row["model_names"]),
    ))
    return rows


def _collect_unreferenced_configs(
    db, config_hits: dict[str, dict], rows: list[dict]
) -> list[dict]:
    """名单命中但清单中无任何落点的配置（仅摘要提示：租户持有配置但无业务引用）。

    model_configs.name 直命中只作反查入口，CSV 保持纯引用清单不单列；租户名批量
    解析（单条 IN 查询，不读联系人字段）。
    """
    used = {row.get("config_id") for row in rows if row.get("config_id")}
    leftover = [(key, hit) for key, hit in config_hits.items() if key not in used]
    if not leftover:
        return []
    tenant_meta = _tenant_names(
        db, {hit["tenant_id"] for _, hit in leftover if hit.get("tenant_id")}
    )
    items = [
        {
            "tenant_id": hit.get("tenant_id"),
            "tenant_name": tenant_meta.get(hit.get("tenant_id") or ""),
            "config_id": key,
            "name": hit["name"],
            "names": sorted(hit["names"]),
        }
        for key, hit in leftover
    ]
    items.sort(key=lambda item: (item["tenant_name"] or "", item["name"] or "", item["config_id"]))
    return items


def _write_csv(rows: list[dict], csv_path: str) -> None:
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_HEADERS)
            for row in rows:
                writer.writerow([
                    row["level"],
                    row.get("tenant_id") or "",
                    row.get("tenant_name") or "",
                    row.get("space_id") or "",
                    row.get("space_name") or "",
                    row["object_id"],
                    row.get("object_name") or "",
                    row["object_type"],
                    "; ".join(row["positions"]),
                    ", ".join(row["model_names"]),
                    row.get("config_id") or "",
                    row.get("remark") or "",
                ])
    except OSError as exc:
        _die(f"无法写入 CSV: {csv_path}: {exc}")


def _provider_anchor() -> str:
    """运行期读取 dashscope 密钥探测锚点，避免报告内写死过期。"""
    try:
        from app.core.model_provider_config import get_provider_validation_model
    except ImportError:
        return "读取失败（人工确认 model_provider_config._VALIDATION_MODELS）"
    return get_provider_validation_model("dashscope") or "未配置"


def _render_summary(
    rows: list[dict],
    names: list[str],
    yaml_marks: dict[str, bool] | None,
    csv_path: str,
    unreferenced: list[dict],
) -> str:
    names_set = set(names)
    by_model: dict[str, dict] = {}
    tenants: set[str] = set()
    for row in rows:
        for name in row["model_names"]:
            stat = by_model.setdefault(name, {"tenants": set(), "refs": 0})
            stat["refs"] += 1
            if row.get("tenant_id"):
                stat["tenants"].add(row["tenant_id"])
        if row.get("tenant_id"):
            tenants.add(row["tenant_id"])

    hit_names = set(by_model)
    held_only = {name for item in unreferenced for name in item["names"]} - hit_names
    for item in unreferenced:
        if item.get("tenant_id"):
            tenants.add(item["tenant_id"])

    lines = [
        f"下线模型影响面审计（{BATCH_LABEL}）",
        f"模型名单 {len(names)} 个；命中 {len(hit_names) + len(held_only)} 个"
        f"（有引用 {len(hit_names)} / 仅持有配置 {len(held_only)}）；"
        f"清单 {len(rows)} 行；涉及租户 {len(tenants)} 个",
        "",
        "按模型分组（命中租户数 / 引用行数）：",
    ]
    for name in sorted(hit_names, key=lambda item: (-by_model[item]["refs"], item)):
        lines.append(f"  {name}  {len(by_model[name]['tenants'])} 租户 / {by_model[name]['refs']} 行")

    zero = [
        name for name in names if name not in hit_names and name not in held_only
    ]
    lines += [
        "",
        f"零命中模型（{len(zero)} 个，无配置/业务引用/渠道/密钥落点）：",
        "  " + (", ".join(zero) or "-"),
    ]

    if yaml_marks is None:
        lines += [
            "",
            "dashscope_models.yaml 读取失败（文件缺失或解析错误）：is_deprecated 对照已跳过，"
            "请人工确认标记状态。",
        ]
    else:
        not_in_yaml = sorted(name for name in hit_names if name not in yaml_marks)
        lines += [
            "",
            f"命中但不在 dashscope_models.yaml（{len(not_in_yaml)} 个，无法用 yaml 标记，需人工处理）：",
            "  " + (", ".join(not_in_yaml) or "-"),
        ]

        unmarked = sorted(name for name in hit_names if yaml_marks.get(name) is False)
        lines += [
            "",
            f"命中且在 yaml 但未标 is_deprecated（{len(unmarked)} 个，预期为空）：",
            "  " + (", ".join(unmarked) or "-"),
        ]

    lines += [
        "",
        f"存在名单模型配置但清单内无落点（{len(unreferenced)} 条，租户持有配置但无业务引用，"
        "无迁移影响，CSV 不单列）：",
    ]
    if unreferenced:
        for item in unreferenced:
            tenant_label = item["tenant_name"] or item["tenant_id"] or "未知租户"
            lines.append(
                f"  {tenant_label} / {item['name']} / {item['config_id']}"
                f"（{'、'.join(item['names'])}）"
            )
    else:
        lines.append("  -")

    anchor = _provider_anchor()
    lines += [
        "",
        "静态清单（代码内引用，非 DB 数据；批次编写时人工核查，提交前建议复核）：",
        f"  - 渠道密钥探测锚点（dashscope）= {anchor}"
        f"（{'命中名单，需替换' if anchor in names_set else '不在名单'}）",
        "  - cv_model.py __main__ demo 使用 qwen-vl-max（非运行路径，可选清理）",
        "",
        f"CSV：{csv_path}（{len(rows)} 行：层级/租户/空间/对象/引用位置/模型名）",
        "说明：模型名精确匹配（大小写敏感）；不含密钥与联系人信息；"
        "model_configs.name 直命中无业务引用的配置不进清单，见上方小节。",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="下线模型影响面审计（只读）：按租户分层输出业务引用清单"
    )
    parser.add_argument("--models-file", help="覆盖内嵌名单（每行一个模型名，# 开头忽略）")
    parser.add_argument(
        "--csv", default="./deprecated_model_audit.csv",
        help="CSV 输出路径（默认 ./deprecated_model_audit.csv）",
    )
    args = parser.parse_args()

    names = _load_names(args.models_file)
    names_set = set(names)
    _load_env()
    for var in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        if not os.getenv(var):
            _die(f"缺少 env {var}；请在 MemoryBear-Enterprise 根或 core/api 目录运行")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(
        f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}/{os.environ['DB_NAME']}",
        pool_pre_ping=True,
    )
    with Session(engine) as db:
        try:
            config_hits, entries = _scan_model_configs(db, names_set)
            entries += _scan_channels(db, names_set)
            api_key_entries, linked_config_names = _scan_api_keys(db, names_set)
            entries += api_key_entries
            names_by_config = {key: set(hit["names"]) for key, hit in config_hits.items()}
            for key, extra_names in linked_config_names.items():
                names_by_config.setdefault(key, set()).update(extra_names)
            refs, bare = _scan_business_refs(
                db, {key: sorted(value) for key, value in names_by_config.items()}, names_set
            )
            entries += refs + bare
            _resolve_meta(db, entries)
            rows = _merge_rows(entries)
            unreferenced = _collect_unreferenced_configs(db, config_hits, rows)
            _write_csv(rows, args.csv)
            print(_render_summary(rows, names, _load_yaml_marks(), args.csv, unreferenced))
        finally:
            db.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
