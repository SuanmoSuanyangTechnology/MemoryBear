"""图数据可视化查询（analytics）。

流程（与老单体 ``user_memory_service.analytics_graph_data`` 对齐）：

1. **模式分派**：传 ``center_node_id`` → Center_Mode（全局 limit 控制邻居总量）；
   否则按 ``node_types`` 走 Filter/Default 模式并解析每类型上限；
2. **取数**（``GraphQueryRepository``）：节点 + 节点间边，仓库层已批量补 ``rel_count``；
3. **整形**：按 label 白名单提取属性、边统一为 UnifiedEdge；
4. **统计**：Q4 逐类型全量计数 → ``statistics.per_type`` 的 returned/total/limit/truncated。

职责边界：**只做查询与装配**。end_user 是否属于调用方 workspace 的鉴权由 controller
在调用前完成，故本层签名里没有 workspace_id。
"""

import uuid
from typing import Any

from src.constants.error_codes import BizCode
from src.constants.graph_data_constants import SUPPORTED_NODE_TYPES
from src.i18n.exceptions import BadRequestError
from src.infrastructure.logger.config import get_logger
from src.repositories.graph_repo import GraphQueryRepository
from src.schemas.graph_data_schema import GraphDataResponse
from src.services._graph_data_helpers import (
    assemble_center_per_type_stat,
    assemble_per_type_stat,
    build_unified_edges,
    compute_stat_types,
    extract_node_properties,
    resolve_edge_caption,
    resolve_mode_and_type_limits,
)
from src.utils.redis_cache import redis_cache

logger = get_logger(__name__)


def _validated(payload: dict[str, Any]) -> dict[str, Any]:
    """过一遍 ``GraphDataResponse`` 校验再出参（契约见 schemas/graph_data_schema.py）。

    校验的意义在于**装配错误当场暴露**：``ge=`` 约束挡住负数计数、必填 ``caption``
    挡住"前端要判空"、``Literal`` 挡住非法 edge_type——否则畸形响应会一路发到前端。

    ``exclude_none=True`` 与老单体一致：剔除边条目里为 None 的可选字段（``predicate``
    等），但**不影响 ``properties`` 字典内部的 None**（未命中展示映射的 emotion 字段
    会保留为 null）。
    """
    return GraphDataResponse.model_validate(payload).model_dump(exclude_none=True)

# 空图响应结构（无节点时的统一形态，与老单体 _empty_graph_response 一致）
_EMPTY_STATISTICS: dict[str, Any] = {
    "total_nodes": 0,
    "total_edges": 0,
    "node_types": {},
    "edge_types": {},
    "per_type": {},
}


class AnalyticsService:
    """图数据查询与响应装配。"""
    @redis_cache(prefix="memory_graph", skip_args=["self"], id_arg="end_user_id")
    async def get_graph(
            self,
            end_user_id: uuid.UUID | str,
            limit: int,
            depth: int,
            node_types: list[str] | None = None,
            center_node_id: str | None = None,
            per_type_limits: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """查询某终端用户的图数据。

        Args:
            end_user_id: 终端用户 ID（已由 controller 校验属于调用方 workspace）。
            limit: 节点数上限。Center_Mode 下是邻居总量上限；其它模式是**兜底**
                每类型上限（未在内置默认表里的类型才用它）。
            depth: Center_Mode 的扩展跳数（仓库层再钳制到 1..DEPTH_HARD_MAX）。
            node_types: 限定的节点类型；None/空表示全部受支持类型。
            center_node_id: Center_Mode 的中心节点 elementId。
            per_type_limits: 每类型上限覆盖（优先生效）。

        Returns:
            ``{"nodes": [...], "edges": [...], "statistics": {...}}``
            - nodes：``{id, label, properties, caption}``，``properties`` 内含
              ``associative_memory``（关联边数）
            - edges：UnifiedEdge（``EXTRACTED_RELATIONSHIP`` 已按节点对聚合）
            - statistics：``total_nodes`` / ``total_edges`` / ``node_types`` /
              ``edge_types`` / ``per_type``

        Raises:
            BadRequestError: ``node_types`` 含不受支持的类型——返回 400 +
                ``INVALID_PARAMETER``，文案 ``analytics.errors.invalid_node_types``。
        """
        euid = str(end_user_id)

        # 0. 入参校验：不受支持的节点类型直接报错，不静默过滤。
        #    老单体的 resolve_mode_and_type_limits 会静默取交集，于是客户端把类型名
        #    拼错时只会拿到空图（"没有数据"），而非"类型不支持"——本服务改为显式
        #    报错（与老单体 cypher builder 的 ValueError 意图一致，那段在老单体里
        #    因先被过滤而从未触达）。Center_Mode 不使用 node_types，但传入非法值
        #    同样是客户端错误，一并拦截。
        unknown_types = [t for t in (node_types or []) if t not in SUPPORTED_NODE_TYPES]
        if unknown_types:
            raise BadRequestError(
                biz_code=BizCode.INVALID_PARAMETER,
                error_key="analytics.errors.invalid_node_types",
                node_types=", ".join(sorted(unknown_types)),
            )

        # 1+2. 模式分派 + 取数
        if center_node_id:
            mode = "Center"
            type_limits: dict[str, int] = {}
            graph = await GraphQueryRepository.get_end_user_graph(
                euid,
                center_node_id=center_node_id,
                depth=depth,
                limit=limit,
            )
        else:
            mode, type_limits = resolve_mode_and_type_limits(
                node_types=node_types, limit=limit, per_type_limits=per_type_limits,
            )
            # limit=0 表示调用方主动跳过该类型，不发起查询
            non_zero_limits = {t: v for t, v in type_limits.items() if v > 0}
            if non_zero_limits:
                graph = await GraphQueryRepository.get_end_user_graph(
                    euid,
                    node_types=list(non_zero_limits),
                    per_type_limits=non_zero_limits,
                    limit=limit,
                )
            else:
                graph = {"nodes": [], "edges": []}

        if not graph["nodes"]:
            # 无节点则无需统计与边，直接空图（保持与老单体一致的响应形状）
            logger.info(
                "get_graph: 空图 end_user=%s mode=%s center=%s",
                euid, mode, center_node_id,
            )
            return _validated({"nodes": [], "edges": [], "statistics": dict(_EMPTY_STATISTICS)})

        # 3. 整形
        nodes, node_type_counts, node_ids = self._assemble_nodes(graph["nodes"])
        edges, edge_type_counts = self._assemble_edges(graph["edges"], node_ids)
        unified_edges = build_unified_edges(edges)

        # 4. 统计（Q4）
        total_by_type = await GraphQueryRepository.count_nodes_by_type(
            euid, compute_stat_types(mode, type_limits)
        )
        if mode == "Center":
            per_type_stat = assemble_center_per_type_stat(
                compute_stat_types(mode, type_limits),
                node_type_counts,
                total_by_type,
                global_limit=limit,
                total_returned=len(nodes),
            )
        else:
            per_type_stat = assemble_per_type_stat(
                compute_stat_types(mode, type_limits),
                type_limits,
                node_type_counts,
                total_by_type,
            )

        statistics = {
            "total_nodes": len(nodes),
            "total_edges": len(unified_edges),
            "node_types": node_type_counts,
            "edge_types": edge_type_counts,
            "per_type": per_type_stat,
        }

        logger.info(
            "get_graph: end_user=%s mode=%s nodes=%d edges=%d per_type=[%s]",
            euid, mode, len(nodes), len(unified_edges),
            ", ".join(
                f"{t}:returned={v['returned']}/total={v['total']}/limit={v['limit']}"
                for t, v in per_type_stat.items()
            ),
        )
        return _validated({"nodes": nodes, "edges": unified_edges, "statistics": statistics})

    @staticmethod
    def _assemble_nodes(
            node_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
        """装配响应节点列表。

        仓库层已批量补好 ``rel_count``（消除老单体时代的 N+1 子查询），此处直接取用。

        Returns:
            ``(nodes, node_type_counts, node_ids)``；``node_ids`` 顺序与 ``nodes`` 一致。
        """
        nodes: list[dict[str, Any]] = []
        node_type_counts: dict[str, int] = {}
        node_ids: list[str] = []

        for row in node_rows:
            node_id = row.get("id")
            if not node_id:
                continue

            labels = row.get("labels") or []
            label = labels[0] if labels else "Unknown"
            # 白名单查询的 map literal 会保留 null（属性不存在时），过滤以对齐
            # properties(n) 的行为：只返回存在的属性
            raw_props = {k: v for k, v in (row.get("properties") or {}).items() if v is not None}
            rel_count = int(row.get("rel_count") or 0)

            properties = extract_node_properties(label, raw_props, rel_count=rel_count)

            nodes.append({
                "id": node_id,
                "label": label,
                "properties": properties,
                # caption 恒存在：属性里没有就回落 label，前端无需判空
                "caption": properties.get("caption", label),
            })
            node_type_counts[label] = node_type_counts.get(label, 0) + 1
            node_ids.append(node_id)

        return nodes, node_type_counts, node_ids

    @staticmethod
    def _assemble_edges(
            edge_rows: list[dict[str, Any]],
            node_ids: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """装配响应边列表。

        仓库层的 Q3 已保证两端都在 ``node_ids`` 内，此处再校验一次（防御并发删除
        等导致的悬空边）。边查询异常由仓库层抛出，本层不吞异常——图数据接口的边
        与节点应同生共死，静默降级会让前端拿到"有节点无边"的误导性结果。

        Returns:
            ``(edges, edge_type_counts)``，``edge_type_counts`` 为 ``{关系类型: 条数}``。
        """
        node_id_set = set(node_ids)
        edges: list[dict[str, Any]] = []
        edge_type_counts: dict[str, int] = {}

        for row in edge_rows:
            source, target = row.get("source"), row.get("target")
            if source not in node_id_set or target not in node_id_set:
                continue

            rel_type = row.get("rel_type")
            props = row.get("properties") or {}
            predicate_description = props.get("predicate_description")

            edges.append({
                "id": row.get("id"),
                "source": source,
                "target": target,
                "type": rel_type,
                "properties": props,
                "caption": resolve_edge_caption(rel_type, props),
                "predicate_description": predicate_description or None,
            })
            edge_type_counts[rel_type] = edge_type_counts.get(rel_type, 0) + 1

        return edges, edge_type_counts