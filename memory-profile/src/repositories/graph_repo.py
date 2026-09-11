"""图数据查询仓储（Neo4j）。

只负责**取数**：按类型/中心节点取节点、取节点间边、取关系计数，返回原始行。
字段裁剪成响应形态、统计装配、caption 回落等展示逻辑留在 service 层。

Cypher 全部来自 ``repositories/cypher/graph_queries.py``（含 label 内联的白名单
校验，见该模块说明）。
"""

import asyncio
from typing import Any

from src.constants.graph_data_constants import (
    DEFAULT_PER_TYPE_LIMIT_MAP,
    SINGLE_TYPE_LIMIT_HARD_MAX,
    SUPPORTED_NODE_TYPES,
    TOTAL_NODES_CAP,
)
from src.infrastructure.logger.config import get_logger
from src.infrastructure.neo4j.client import execute_read_query
from src.repositories.cypher.graph_queries import (
    GRAPH_EDGES_AMONG_NODES,
    GRAPH_NODES_REL_COUNT_BATCH,
    build_center_node_neighbors_query,
    build_graph_nodes_by_type_query,
    build_graph_total_count_by_type_query,
)

logger = get_logger(__name__)


class GraphQueryRepository:
    """Neo4j 图数据读取。"""

    @staticmethod
    async def get_end_user_graph(
            end_user_id: str,
            *,
            node_types: list[str] | None = None,
            per_type_limits: dict[str, int] | None = None,
            center_node_id: str | None = None,
            depth: int = 1,
            limit: int = 100,
            total_cap: int = TOTAL_NODES_CAP,
    ) -> dict[str, list[dict[str, Any]]]:
        """取某终端用户的图数据（节点 + 节点间边）。

        两种模式（与老单体 ``_collect_node_query`` 一致）：

        - **Center_Mode**（传 ``center_node_id``）：以该节点为中心取 1..``depth`` 跳
          邻居（含中心节点），受 ``limit`` 限制；``node_types``/``per_type_limits``
          不参与。
        - **全量模式**：按 ``node_types``（缺省为 ``SUPPORTED_NODE_TYPES`` 全部）
          逐类型查询，每类型取 ``per_type_limits`` 中该类型的上限（缺省用
          ``DEFAULT_PER_TYPE_LIMIT_MAP``），单类型上限先钳到
          ``SINGLE_TYPE_LIMIT_HARD_MAX``，累计不超过 ``total_cap``。

        Args:
            end_user_id: 终端用户 ID（调用方须已校验其属于本次请求的 workspace）。
            node_types: 限定的节点类型；None 或空列表表示全部受支持类型。
            per_type_limits: 每类型条数上限覆盖；None 用默认表。
            center_node_id: Center_Mode 的中心节点 elementId；None 为全量模式。
            depth: Center_Mode 跳数（内部再钳制到 1..DEPTH_HARD_MAX）。
            limit: Center_Mode 的节点上限 / 全量模式的兜底上限。
            total_cap: 全量模式节点总量硬上限。

        Returns:
            ``{"nodes": [...], "edges": [...]}``：
            - 节点行 ``{"id", "labels", "properties", "rel_count"}``
            - 边行 ``{"id", "source", "target", "rel_type", "properties"}``
            - ``properties`` 中的 null 值未过滤、neo4j 时间类型已转 ISO 字符串。

        Raises:
            ValueError: ``node_types`` 含不受支持的类型（拒绝内联进 Cypher）。
        """
        types = list(node_types or SUPPORTED_NODE_TYPES)
        unknown = [t for t in types if t not in SUPPORTED_NODE_TYPES]
        if unknown:
            # 先整体校验再发查询：避免查到一半才报错
            raise ValueError(f"不支持的节点类型: {', '.join(sorted(unknown))}")

        if center_node_id:
            nodes = await GraphQueryRepository._fetch_center_nodes(
                end_user_id, center_node_id, depth, limit
            )
        else:
            nodes = await GraphQueryRepository._fetch_nodes_by_type(
                end_user_id, types, per_type_limits, limit, total_cap
            )

        if not nodes:
            # 无节点则必然无边，省掉两次往返
            return {"nodes": [], "edges": []}

        node_ids = [n["id"] for n in nodes]
        nodes = await GraphQueryRepository._attach_rel_counts(nodes, node_ids)
        edges = await execute_read_query(GRAPH_EDGES_AMONG_NODES, node_ids=node_ids)

        logger.info(
            "get_end_user_graph: end_user=%s mode=%s nodes=%d edges=%d",
            end_user_id, "center" if center_node_id else "full", len(nodes), len(edges),
        )
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    async def count_nodes_by_type(
            end_user_id: str,
            node_types: list[str] | None = None,
    ) -> dict[str, int]:
        """Q4：逐类型统计该用户的节点总数（供 ``statistics.per_type`` 的 total）。

        各类型独立计数（label 内联以命中索引），故并发发起；单类型失败降级为 0
        并记日志——统计口径缺失不该让整个图数据接口失败。

        Returns:
            ``{类型: 总数}``；未查询/查询失败的类型不出现（调用方按 0 处理）。
        """
        types = list(node_types or SUPPORTED_NODE_TYPES)
        unknown = [t for t in types if t not in SUPPORTED_NODE_TYPES]
        if unknown:
            raise ValueError(f"不支持的节点类型: {', '.join(sorted(unknown))}")

        async def _count(node_type: str) -> tuple[str, int]:
            try:
                rows = await execute_read_query(
                    build_graph_total_count_by_type_query(node_type),
                    end_user_id=end_user_id,
                )
                return node_type, int(rows[0]["total"]) if rows else 0
            except Exception:
                logger.warning("统计节点数失败，按 0 处理: type=%s", node_type, exc_info=True)
                return node_type, 0

        return dict(await asyncio.gather(*(_count(t) for t in types)))

    @staticmethod
    async def _fetch_nodes_by_type(
            end_user_id: str,
            types: list[str],
            per_type_limits: dict[str, int] | None,
            limit: int,
            total_cap: int,
    ) -> list[dict[str, Any]]:
        """全量模式：逐类型取节点，受单类型上限与总量上限约束。"""
        overrides = per_type_limits or {}
        collected: list[dict[str, Any]] = []

        for node_type in types:
            remaining = total_cap - len(collected)
            if remaining <= 0:
                logger.info("达到节点总量上限 %d，停止取数", total_cap)
                break

            wanted = overrides.get(node_type, DEFAULT_PER_TYPE_LIMIT_MAP.get(node_type, limit))
            # 单类型上限硬钳 + 不越过剩余总量
            take = max(1, min(int(wanted), SINGLE_TYPE_LIMIT_HARD_MAX, remaining))

            rows = await execute_read_query(
                build_graph_nodes_by_type_query(node_type),
                end_user_id=end_user_id,
                limit=take,
            )
            collected.extend(rows)

        return collected

    @staticmethod
    async def _fetch_center_nodes(
            end_user_id: str,
            center_node_id: str,
            depth: int,
            limit: int,
    ) -> list[dict[str, Any]]:
        """Center_Mode：取中心节点及其 1..depth 跳邻居。"""
        return await execute_read_query(
            build_center_node_neighbors_query(depth),
            end_user_id=end_user_id,
            center_node_id=center_node_id,
            limit=limit,
        )

    @staticmethod
    async def _attach_rel_counts(
            nodes: list[dict[str, Any]],
            node_ids: list[str],
    ) -> list[dict[str, Any]]:
        """批量补 ``rel_count``（节点关联边数），一次查询取代 N+1。

        查不到的节点（并发删除等）补 0，保证 ``rel_count`` 恒存在，调用方无需判空。
        """
        counts = {
            row["id"]: row["rel_count"]
            for row in await execute_read_query(
                GRAPH_NODES_REL_COUNT_BATCH, node_ids=node_ids
            )
        }
        for node in nodes:
            node["rel_count"] = counts.get(node["id"], 0)
        return nodes


__all__ = ["GraphQueryRepository"]