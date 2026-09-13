"""工作流模型引用扫描（M6 §18.2）：只读报告 workflow 节点里引用的模型配置。

背景：工作流节点把模型写进 JSON（`workflow_configs.nodes` 草稿与 `app_releases.config`
发布快照），不落外键，影响面清单（model_impact_service）覆盖不到。删除/停用模型前用本
脚本核对：哪些应用/工作流节点仍在引用。

- 递归扫节点 JSON 中的 `model_id` / `model_config_id` / `default_model_config_id`
  （兼容节点被 `data` 包裹的形态），不依赖具体节点 schema；
- 引用批量解析模型名（单查询，无 N+1）；`--model-config <uuid>` 只报告该模型的引用；
- 只读：不写库、不改 JSON。

用法（连接读 env，自动加载 enterprise 根 .env 其次 core/api/.env）：
    python core/api/scripts/scan_workflow_model_refs.py
    python core/api/scripts/scan_workflow_model_refs.py --model-config <model_config_id>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # core/api

_REPO_CANDIDATES = [
    Path(__file__).resolve().parents[3] / ".env",   # MemoryBear-Enterprise 根
    Path(__file__).resolve().parents[1] / ".env",   # core/api
]

_REF_KEYS = ("model_id", "model_config_id", "default_model_config_id")


def _collect_refs(payload, refs: dict) -> None:
    """递归收集 payload 中 _REF_KEYS 的取值（{key: [值...]}，保序去重）。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _REF_KEYS and isinstance(value, str) and value:
                refs.setdefault(key, [])
                if value not in refs[key]:
                    refs[key].append(value)
            else:
                _collect_refs(value, refs)
    elif isinstance(payload, list):
        for item in payload:
            _collect_refs(item, refs)


def _scan_nodes(nodes) -> list[tuple[str, str, dict]]:
    """节点列表 → [(node_id, node_type, refs)]（refs 非空的节点）。"""
    hits = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        refs: dict = {}
        _collect_refs(node, refs)
        if refs:
            hits.append(
                (
                    str(node.get("id") or "-"),
                    str(node.get("type") or node.get("node_type") or "-"),
                    refs,
                )
            )
    return hits


def _draft_hits(db) -> list[dict]:
    """workflow_configs 草稿（JOIN apps 取名）。"""
    from app.models.app_model import App
    from app.models.workflow_model import WorkflowConfig

    rows = (
        db.query(WorkflowConfig.app_id, WorkflowConfig.nodes, App.name)
        .join(App, App.id == WorkflowConfig.app_id)
        .all()
    )
    hits: list[dict] = []
    for app_id, nodes, app_name in rows:
        for node_id, node_type, refs in _scan_nodes(nodes):
            for key, values in refs.items():
                for value in values:
                    hits.append(
                        {
                            "source": "draft",
                            "app_id": str(app_id),
                            "app_name": app_name,
                            "version_name": None,
                            "is_active": None,
                            "node_id": node_id,
                            "node_type": node_type,
                            "key": key,
                            "value": value,
                        }
                    )
    return hits


def _release_hits(db) -> list[dict]:
    """app_releases 发布快照（config.nodes；含未生效版本，is_active 标注）。"""
    from app.models.app_model import App
    from app.models.app_release_model import AppRelease

    rows = (
        db.query(
            AppRelease.app_id,
            AppRelease.version_name,
            AppRelease.is_active,
            AppRelease.config,
            App.name,
        )
        .join(App, App.id == AppRelease.app_id)
        .all()
    )
    hits: list[dict] = []
    for app_id, version_name, is_active, config, app_name in rows:
        nodes = (config or {}).get("nodes") if isinstance(config, dict) else None
        for node_id, node_type, refs in _scan_nodes(nodes):
            for key, values in refs.items():
                for value in values:
                    hits.append(
                        {
                            "source": "release",
                            "app_id": str(app_id),
                            "app_name": app_name,
                            "version_name": version_name,
                            "is_active": bool(is_active),
                            "node_id": node_id,
                            "node_type": node_type,
                            "key": key,
                            "value": value,
                        }
                    )
    return hits


def _resolve_models(db, config_ids: set[str]) -> dict:
    """批量解析模型名（单查询）；非法 UUID 值不入 SQL。"""
    import uuid as _uuid

    from app.models.models_model import ModelConfig

    uuids = []
    for value in config_ids:
        try:
            uuids.append(_uuid.UUID(value))
        except (ValueError, AttributeError, TypeError):
            continue
    if not uuids:
        return {}
    rows = (
        db.query(ModelConfig.id, ModelConfig.name, ModelConfig.tenant_id)
        .filter(ModelConfig.id.in_(uuids))
        .all()
    )
    return {
        str(row.id): {"name": row.name, "tenant_id": str(row.tenant_id) if row.tenant_id else None}
        for row in rows
    }


def _render(hits: list[dict], models: dict, model_config: str | None) -> str:
    by_value: dict[str, list[dict]] = {}
    for hit in hits:
        by_value.setdefault(hit["value"], []).append(hit)

    title = "工作流模型引用扫描报告"
    if model_config:
        title += f"（仅 model_config_id={model_config}）"
    lines = [title, f"引用总数：{len(hits)}；涉及模型配置：{len(by_value)}", "明细："]
    for value in sorted(by_value, key=lambda key: models.get(key, {}).get("name") or key):
        meta = models.get(value)
        label = f"{meta['name']}（{value}）" if meta else f"[未知模型配置] {value}"
        lines.append(f"  {label}  引用 {len(by_value[value])} 处")
        for hit in sorted(
            by_value[value],
            key=lambda item: (item["app_name"], item["source"], item["node_id"]),
        ):
            version = f"@{hit['version_name']}" if hit["version_name"] else ""
            active = (
                "生效" if hit["is_active"] else "未生效" if hit["is_active"] is not None else "-"
            )
            lines.append(
                f"      [{hit['source']}] {hit['app_name']}{version}({active})"
                f" 节点 {hit['node_type']}/{hit['node_id']}  {hit['key']}"
            )
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", help="只报告该 model_config_id 的引用")
    args = parser.parse_args()

    _load_env()
    for var in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        if not os.getenv(var):
            print(f"[err] 缺少 env {var}；请在 MemoryBear-Enterprise 根或 core/api 目录运行")
            return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(
        f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}/{os.environ['DB_NAME']}",
        pool_pre_ping=True,
    )
    with Session(engine) as db:
        try:
            hits = _draft_hits(db) + _release_hits(db)
            if args.model_config:
                hits = [hit for hit in hits if hit["value"] == args.model_config]
            models = _resolve_models(db, {hit["value"] for hit in hits})
            print(_render(hits, models, args.model_config))
        finally:
            db.rollback()
    return 0


def _load_env() -> None:
    try:
        import dotenv
    except ImportError:
        return
    for env_path in _REPO_CANDIDATES:
        if env_path.is_file():
            dotenv.load_dotenv(env_path)


if __name__ == "__main__":
    raise SystemExit(main())
