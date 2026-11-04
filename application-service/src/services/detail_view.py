"""详情页图形区的判别对象（方案 §6.1.3）——纯函数，无 ORM / 无 DB 依赖。

为什么单独成模块：`asset_service.py` 会连带导入整棵模型树（`..models`），
在只有标准库的环境里根本 import 不起来，投影逻辑就永远测不到。本模块只依赖
标准库，可以脱离数据库直接单测，这也是「先证明投影对不对，再谈存进去什么」的
前提。

`market-service/src/services/bundle_view.py` 里有一份行为完全相同的实现——两个
服务互相 import 不到（独立进程、独立部署）。两份必须同步改，
`tests/test_detail_view.py` 用同一组用例比对两边输出，不一致就红。
"""
from __future__ import annotations

from typing import Any

# 工作流一族：`pure_workflow` 与 `workflow` 的快照同构，图上没有区别
WORKFLOW_TYPES = ("workflow", "pure_workflow")

CLUSTER_TYPE = "multi_agent"

# 结构异常时的兜底：图区不渲染，但不影响详情页其余部分
AGENT_VIEW: dict[str, Any] = {"kind": "agent", "workflow": None, "cluster": None}

DEFAULT_SUPERVISOR_NAME = "主管"


def normalise_cluster_mode(mode: Any) -> tuple[str, str]:
    """原始 `orchestration_mode` → 展示用的 `(orchestration_mode, execution)`。

    四档归两档，判定顺序必须与运行时 `_normalize_orchestration_mode`
    （`multi_agent_orchestrator.py`）一致：`supervisor_loop` 是 `supervisor` 的
    前缀超集，先判 `supervisor` 会把它吞掉（运行时那边也为此写了回归测试）。

    `conditional` / `sequential` / `parallel` / `loop` / `null` 都是历史值，运行时
    一律归一为协作，展示不能与运行不一致。
    """

    value = str(mode or "").strip().lower()
    if value in ("supervisor_loop", "supervisor"):
        return "supervisor", "parallel"
    return "collaboration", "sequential"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def project_workflow(config: dict[str, Any]) -> dict[str, Any]:
    """工作流图：只留画图要用的字段。

    节点 `config` 里带 `url` / `headers` / `model_id` / `knowledge_bases`。bundle
    的两道闸都挡不住它们：`redact_secrets` 只按**键名**剥凭据（`asset_bundle.py`
    的 `SECRET_KEY_NAMES`），`assert_no_private_refs` 的禁用后缀表又不含
    `model_id` / `knowledge_bases`。所以投影阶段就裁掉，不依赖红名单兜底。
    节点 `position` 同理不下发——布局由前端算。
    """

    nodes: list[dict[str, Any]] = []
    for raw in _as_list(config.get("nodes")):
        if not isinstance(raw, dict):
            continue
        node_id = raw.get("id")
        if node_id is None:
            continue
        nodes.append(
            {
                "id": str(node_id),
                "name": str(raw.get("name") or ""),
                "type": str(raw.get("type") or ""),
                "description": raw.get("description"),
            }
        )

    edges: list[dict[str, Any]] = []
    for raw in _as_list(config.get("edges")):
        if not isinstance(raw, dict):
            continue
        source, target = raw.get("source"), raw.get("target")
        if source is None or target is None:
            continue
        edges.append(
            {
                "source": str(source),
                "target": str(target),
                "label": raw.get("label"),
                "condition": raw.get("condition"),
            }
        )

    return {"node_count": len(nodes), "nodes": nodes, "edges": edges}


def project_cluster(config: dict[str, Any]) -> dict[str, Any]:
    """集群图：主管 + 子智能体。

    `agent_id` / `release_id` 不下发：那是源租户的私有引用，跨租户无意义，市场
    详情页也不需要点进子 Agent 看。
    """

    mode, execution = normalise_cluster_mode(config.get("orchestration_mode"))

    supervisor = config.get("supervisor_config")
    supervisor_name = None
    if isinstance(supervisor, dict):
        supervisor_name = supervisor.get("name")
    supervisor_name = supervisor_name or config.get("master_agent_name")

    sub_agents: list[dict[str, Any]] = []
    for raw in _as_list(config.get("sub_agents")):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if name is None:
            continue
        # 图上子智能体卡片下面那行小字就是 role
        sub_agents.append({"name": str(name), "description": raw.get("role")})

    return {
        "orchestration_mode": mode,
        "execution": execution,
        "supervisor_name": (
            str(supervisor_name) if supervisor_name else DEFAULT_SUPERVISOR_NAME
        ),
        "sub_agents": sub_agents,
    }


def build_detail_view(app_type: Any, config: Any) -> dict[str, Any]:
    """`kind` 判别对象：`workflow` / `cluster` / `agent`。

    三者互斥，未命中的分支为 `None`。`config` 为空或结构异常时一律降级为
    `agent`，**不抛错**——详情页画不出图是一回事，整个详情 500 是另一回事。
    """

    kind = str(app_type or "")
    if kind not in WORKFLOW_TYPES and kind != CLUSTER_TYPE:
        return dict(AGENT_VIEW)

    if not isinstance(config, dict) or not config:
        return dict(AGENT_VIEW)

    if kind in WORKFLOW_TYPES:
        return {"kind": "workflow", "workflow": project_workflow(config), "cluster": None}

    return {"kind": "cluster", "workflow": None, "cluster": project_cluster(config)}


__all__ = [
    "AGENT_VIEW",
    "CLUSTER_TYPE",
    "DEFAULT_SUPERVISOR_NAME",
    "WORKFLOW_TYPES",
    "build_detail_view",
    "normalise_cluster_mode",
    "project_cluster",
    "project_workflow",
]