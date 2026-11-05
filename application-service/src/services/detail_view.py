"""详情页 `display_payload` 的投影——纯函数，无 ORM / 无 DB 依赖。

只投影「图上要画的东西」，不投影配置本体：agent 恒 `None`（详情页没有图形区，
配置也不对外展示）；集群只给主管与子 Agent；工作流给带坐标的节点与连线。

为什么单独成模块：`asset_service.py` 会连带导入整棵模型树（`..models`），
在只有标准库的环境里根本 import 不起来，投影逻辑就永远测不到。本模块只依赖
标准库，可以脱离数据库直接单测。

`market-service/src/services/bundle_view.py` 里有一份行为完全相同的实现——两个
服务互相 import 不到（独立进程、独立部署）。两份必须同步改，
`tests/test_detail_view.py` 用同一组用例比对两边输出，不一致就红。
"""
from __future__ import annotations

from typing import Any

# 工作流一族：`pure_workflow` 与 `workflow` 的快照同构，图上没有区别
WORKFLOW_TYPES = ("workflow", "pure_workflow")

CLUSTER_TYPE = "multi_agent"

# 结构异常时一律降级：没有图形区，但不影响详情页其余部分
AGENT_VIEW: None = None

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


def _node_position(raw: Any) -> dict[str, float] | None:
    """节点坐标：只取 `x` / `y` 两个数值，其余一律丢掉。

    前端按快照里的原坐标摆图，不存在就返回 `None` 让它自己算——不能把原始
    `position` 整个透传，那里面还可能有 `width` / `selected` 之类的编辑器状态。
    """

    if not isinstance(raw, dict):
        return None
    x, y = raw.get("x"), raw.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return {"x": float(x), "y": float(y)}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# 有异常分支出口的节点类型：只有 `error_handle.method == "branch"` 时才有
# SUCCESS / ERROR 两个出口，否则只有一个默认出口（前端不加额外端口）。
ERROR_HANDLE_TYPES = ("llm", "code", "agent", "http-request")


def _branches_of(node_type: str, config: dict[str, Any]) -> list[dict[str, str]]:
    """节点的右侧出口清单（`[{label, name}]`），供前端还原端口与连线。

    编辑器按节点类型生成固定数量的右侧端口（`core/web` 的
    `workflowGraphLoad.ts:124-198`），边靠 `label` 匹配端口
    （同文件 `:317-359`）。所以出口的数量与顺序必须与编辑器一致：

    - `if-else`：`cases` 条 IF/ELIF + 末尾一个 ELSE，共 `cases+1` 个
    - `question-classifier`：每个分类一个 CASE，`name` 是分类名
    - `human-intervention`：每个按钮一个 CASE + 末尾 TIMEOUT
    - `llm` / `code` / `agent` / `http-request`：仅 `error_handle.method ==
      "branch"` 时多出 ERROR 出口

    只取分类名与按钮文案这类展示字段，不碰节点 `config` 的其余部分。
    """

    if node_type == "if-else":
        cases = _as_list(config.get("cases"))
        if not cases:
            return []
        branches = [
            {"label": f"CASE{i}", "name": "IF" if i == 1 else "ELIF"}
            for i in range(1, len(cases) + 1)
        ]
        branches.append({"label": f"CASE{len(cases) + 1}", "name": "ELSE"})
        return branches

    if node_type == "question-classifier":
        branches = []
        for i, item in enumerate(_as_list(config.get("categories")), start=1):
            branches.append(
                {"label": f"CASE{i}", "name": str(_as_dict(item).get("class_name") or "")}
            )
        return branches

    if node_type == "human-intervention":
        branches = []
        for i, item in enumerate(_as_list(config.get("actions")), start=1):
            branches.append(
                {"label": f"CASE{i}", "name": str(_as_dict(item).get("label") or "")}
            )
        branches.append({"label": "TIMEOUT", "name": "超时"})
        return branches

    if node_type in ERROR_HANDLE_TYPES:
        method = str(_as_dict(config.get("error_handle")).get("method") or "").strip().lower()
        if method == "branch":
            return [
                {"label": "SUCCESS", "name": "成功"},
                {"label": "ERROR", "name": "异常分支"},
            ]
        return []

    return []


def project_workflow(config: dict[str, Any]) -> dict[str, Any]:
    """工作流图：只留画图要用的字段。

    节点 `config` 里带 `url` / `headers` / `model_id` / `knowledge_bases`。bundle
    的两道闸都挡不住它们：`redact_secrets` 只按**键名**剥凭据（`asset_bundle.py`
    的 `SECRET_KEY_NAMES`），`assert_no_private_refs` 的禁用后缀表又不含
    `model_id` / `knowledge_bases`。所以投影阶段就裁掉，不依赖红名单兜底。

    节点按 `id` 为单位下发 `name` / `type` / `position` / `parent_id` /
    `branches`：`position` 是快照里作者自己摆好的坐标，`parent_id` 是所属
    loop/iteration 容器（对应快照的 `cycle`），`branches` 是右侧出口清单。
    节点 `description` 不下发。
    """

    nodes: list[dict[str, Any]] = []
    for raw in _as_list(config.get("nodes")):
        if not isinstance(raw, dict):
            continue
        node_id = raw.get("id")
        if node_id is None:
            continue
        node_type = str(raw.get("type") or "")
        parent_id = raw.get("cycle")
        nodes.append(
            {
                "id": str(node_id),
                "name": str(raw.get("name") or ""),
                "type": node_type,
                "position": _node_position(raw.get("position")),
                # 循环 / 迭代容器归属：前端据此把孩子节点塞进容器
                "parent_id": str(parent_id) if parent_id else None,
                "branches": _branches_of(node_type, _as_dict(raw.get("config"))),
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
                # 异常边（运行时不走常规路由）：与 label=ERROR 是两套标记，都给
                "type": (
                    "error"
                    if str(raw.get("type") or "").strip().lower() == "error"
                    else "normal"
                ),
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


def build_display_payload(app_type: Any, config: Any) -> dict[str, Any] | None:
    """`display_payload`：只给图形区要画的东西，`kind` 判别 `workflow` / `cluster`。

    - `agent` / 未知类型：`None`——详情页没有图形区，配置本身也不对外展示
    - `workflow` / `pure_workflow`：`{"kind": "workflow", "workflow": ...}`
    - `multi_agent`：`{"kind": "cluster", "cluster": ...}`

    `config` 为空或结构异常时同样给 `None`，**不抛错**——详情页画不出图是一回事，
    整个详情 500 是另一回事。
    """

    kind = str(app_type or "")
    if kind not in WORKFLOW_TYPES and kind != CLUSTER_TYPE:
        return AGENT_VIEW

    if not isinstance(config, dict) or not config:
        return AGENT_VIEW

    if kind in WORKFLOW_TYPES:
        return {"kind": "workflow", "workflow": project_workflow(config)}

    return {"kind": "cluster", "cluster": project_cluster(config)}


__all__ = [
    "AGENT_VIEW",
    "CLUSTER_TYPE",
    "DEFAULT_SUPERVISOR_NAME",
    "WORKFLOW_TYPES",
    "build_display_payload",
    "normalise_cluster_mode",
    "project_cluster",
    "project_workflow",
]