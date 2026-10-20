"""Tenant-filtered Neo4j analytics queries."""

from __future__ import annotations

from typing import Any

from src.infrastructure.neo4j import Neo4jManager
from src.modules.analytics.constants import (
    COMMUNITY_GRAPH_ENTITY_FIELDS,
    COMMUNITY_GRAPH_NODE_FIELDS,
    EMOTION_TYPE_STORAGE_VALUES,
    NODE_PROPERTY_WHITELIST,
    SUPPORTED_NODE_TYPES,
    WHITELIST_DEFAULT_FIELDS,
)


def whitelist_projection(label: str) -> str:
    """构建单 label 的白名单 Cypher map projection。

    ``properties(n)`` 会把节点上的**全部**属性序列化回传，其中包含
    ``statement_embedding`` / ``chunk_embedding`` / ``name_embedding`` 等
    1024 维向量；而 service 层的 ``_properties()`` 只保留白名单字段，向量随即
    被丢弃。因此把投影下推到 Cypher，只回传真正会进入响应的字段。

    字段名取自 :data:`NODE_PROPERTY_WHITELIST`（代码内常量，非请求入参），
    不存在注入风险。
    """

    fields = NODE_PROPERTY_WHITELIST.get(label, WHITELIST_DEFAULT_FIELDS)
    return ", ".join(f"{field}: n.{field}" for field in fields)


def variable_projection(alias: str, fields: tuple[str, ...]) -> str:
    """构建「某个 Cypher 变量上指定字段」的 map projection。

    与 :func:`whitelist_projection` 的区别：后者按 label 从
    :data:`NODE_PROPERTY_WHITELIST` 取字段，这里由调用方直接给出字段元组 ——
    社区图谱的字段来自 :data:`COMMUNITY_GRAPH_*_FIELDS`，与其 Python 侧过滤
    同源，避免「查什么」和「取什么」两份清单各自漂移。

    字段名全部是代码内常量，非请求入参，不存在注入风险。
    """

    return "{" + ", ".join(f"{field}: {alias}.{field}" for field in fields) + "}"


# Center_Mode 单次查询会返回混合 label 的邻居，按 ``labels(n)[0]`` 分派到各自的白名单
# （api ``build_center_node_neighbors_query`` 同款）。原先取全部白名单字段的并集，每个
# 邻居都要读 30 个属性、回传 30 个键（其中大部分是 null），实测同一请求 DB accesses
# 8161 → 4557、载荷 27.5 KB → 11.6 KB。未登记的 label 回落到只有 caption。
_CENTER_PROPERTY_CASE = (
    "CASE labels(n)[0]\n"
    + "\n".join(
        f"                    WHEN '{label}' THEN {variable_projection('n', fields)}"
        for label, fields in sorted(NODE_PROPERTY_WHITELIST.items())
    )
    + f"\n                    ELSE {variable_projection('n', WHITELIST_DEFAULT_FIELDS)} END"
)

# 社区图谱（``community_graph_rows``）的三个投影：与 service 侧的
# ``COMMUNITY_GRAPH_*_FIELDS`` 同源，且在此一次性构建，不在每请求里重建。
_COMMUNITY_NODE_PROJECTION = variable_projection("c", COMMUNITY_GRAPH_NODE_FIELDS)
_COMMUNITY_ENTITY_PROJECTION = variable_projection("e", COMMUNITY_GRAPH_ENTITY_FIELDS)
_COMMUNITY_NEIGHBOR_PROJECTION = variable_projection("e2", COMMUNITY_GRAPH_ENTITY_FIELDS)

# 工作空间统计的 ES 缺失用户回退计数（api
# ``WorkspaceStatisticsStorage.get_statistics`` 同款 Cypher）。
_WORKSPACE_FALLBACK_STATISTICS_QUERY = """
UNWIND $end_user_ids AS end_user_id
CALL (end_user_id) {
    OPTIONAL MATCH (summary:MemorySummary {end_user_id: end_user_id})
    WHERE summary.delete_at IS NULL
    RETURN count(DISTINCT summary) AS episodic_count
}
CALL (end_user_id) {
    OPTIONAL MATCH (entity:ExtractedEntity {end_user_id: end_user_id})
    WHERE entity.delete_at IS NULL
      AND entity.is_explicit_memory = true
    RETURN count(DISTINCT entity) AS explicit_entity_count
}
CALL (end_user_id) {
    OPTIONAL MATCH (statement:Statement {end_user_id: end_user_id})
    WHERE statement.delete_at IS NULL
      AND statement.emotion_type IS NOT NULL
    RETURN count(DISTINCT statement) AS emotional_count
}
RETURN sum(episodic_count) AS episodic_count,
       sum(explicit_entity_count) AS explicit_entity_count,
       sum(emotional_count) AS emotional_count
"""

# 隐性记忆按用户统计「有来源关系的情景摘要」，单个用户不足 5 条时贡献 0
# （api ``MIN_MEMORY_SUMMARY_COUNT`` 同值同口径）。
_WORKSPACE_IMPLICIT_MEMORY_QUERY = """
UNWIND $end_user_ids AS end_user_id
OPTIONAL MATCH (summary:MemorySummary {end_user_id: end_user_id})
      -[:DERIVED_FROM_STATEMENT]->(:Statement)
WHERE summary.delete_at IS NULL
WITH end_user_id, count(DISTINCT summary) AS summary_count
RETURN sum(
    CASE WHEN summary_count >= $minimum_summary_count
         THEN summary_count ELSE 0 END
) AS implicit_count
"""


class MemoryGraphRepository:
    def __init__(self, neo4j: Neo4jManager) -> None:
        self.neo4j = neo4j

    async def _query(self, query: str, **params: Any) -> list[dict[str, Any]]:
        # 会话/访问模式/事务形态统一由 infra 的 Neo4jManager.read_query 决定
        # （为什么不用 driver.execute_query，见该方法的 docstring）。
        return await self.neo4j.read_query(query, **params)

    async def nodes_by_type(
        self, end_user_id: str, node_type: str, limit: int
    ) -> list[dict[str, Any]]:
        if node_type not in SUPPORTED_NODE_TYPES:
            raise ValueError("unsupported node type")
        # LIMIT 必须落在展开之前：``count(rel)`` 是聚合，聚合先于 LIMIT 执行，于是
        # 「全部匹配节点 × 其全部关系」都要展开一遍，代价与类型规模成正比、与本参数
        # limit 无关（实测同一用户 limit=1 与 limit=50 均为 1116 次 DB access；把截断
        # 提到 WITH 里之后 limit=1 降到 128）。ORDER BY 一并移入 WITH，采样仍是
        # elementId 升序的确定性结果，逐行 (id, rel_count) 与原写法实测完全一致。
        query = f"""
            MATCH (n:`{node_type}`)
            WHERE n.end_user_id = $end_user_id AND n.delete_at IS NULL
            WITH n ORDER BY elementId(n) LIMIT $limit
            OPTIONAL MATCH (n)-[rel]-(other)
            WHERE other.end_user_id = $end_user_id
              AND rel.delete_at IS NULL AND other.delete_at IS NULL
            RETURN elementId(n) AS id, labels(n)[0] AS label,
                   {{{whitelist_projection(node_type)}}} AS properties,
                   count(rel) AS rel_count
        """
        return await self._query(query, end_user_id=end_user_id, limit=limit)

    async def centered_nodes(
        self, end_user_id: str, center_node_id: str, depth: int, limit: int
    ) -> list[dict[str, Any]]:
        # 与 api ``build_center_node_neighbors_query`` 同形：路径用强制 MATCH，
        # 中心节点没有任何可用邻居时整体返回空（而不是只返回中心节点本身）；
        # 路径上的节点与关系都必须未软删除。
        query = f"""
            MATCH path=(center)-[*1..{depth}]-(connected)
            WHERE center.end_user_id = $end_user_id
              AND elementId(center) = $center_node_id
              AND all(node IN nodes(path) WHERE node.delete_at IS NULL)
              AND all(edge IN relationships(path) WHERE edge.delete_at IS NULL)
            WITH collect(DISTINCT center) + collect(DISTINCT connected) AS all_nodes
            UNWIND all_nodes AS n
            WITH DISTINCT n LIMIT $limit
            OPTIONAL MATCH (n)-[rel]-(other)
            WHERE other.end_user_id = $end_user_id
              AND rel.delete_at IS NULL AND other.delete_at IS NULL
            RETURN elementId(n) AS id, labels(n)[0] AS label,
                   {_CENTER_PROPERTY_CASE} AS properties,
                   count(rel) AS rel_count
        """
        return await self._query(
            query,
            end_user_id=end_user_id,
            center_node_id=center_node_id,
            limit=limit,
        )

    async def count_type(self, end_user_id: str, node_type: str) -> int:
        if node_type not in SUPPORTED_NODE_TYPES:
            raise ValueError("unsupported node type")
        rows = await self._query(
            f"""MATCH (n:`{node_type}`)
            WHERE n.end_user_id = $end_user_id AND n.delete_at IS NULL
            RETURN count(n) AS count""",
            end_user_id=end_user_id,
        )
        return int(rows[0]["count"] if rows else 0)

    async def edges(self, end_user_id: str, node_ids: list[str]) -> list[dict[str, Any]]:
        if not node_ids:
            return []
        return await self._query(
            """
            MATCH (a)-[r]->(b)
            WHERE elementId(a) IN $node_ids AND elementId(b) IN $node_ids
              AND a.end_user_id = $end_user_id AND b.end_user_id = $end_user_id
              AND a.delete_at IS NULL AND b.delete_at IS NULL AND r.delete_at IS NULL
            RETURN elementId(r) AS id, type(r) AS type,
                   elementId(a) AS source, elementId(b) AS target,
                   {
                       caption: r.caption,
                       created_at: r.created_at,
                       valid_at: r.valid_at,
                       predicate: r.predicate,
                       predicate_surface: r.predicate_surface,
                       predicate_description: r.predicate_description
                   } AS properties
            """,
            end_user_id=end_user_id,
            node_ids=node_ids,
        )

    async def community_graph_rows(self, end_user_id: str) -> list[dict[str, Any]]:
        """社区 / 实体 / 关系的原始行。

        ``c`` / ``e`` / ``e2`` 只取响应会用到的字段（:data:`COMMUNITY_GRAPH_NODE_FIELDS`
        / :data:`COMMUNITY_GRAPH_ENTITY_FIELDS`），因为 ``properties()`` 会把
        ``Community.summary_embedding``、``ExtractedEntity.name_embedding`` 这类
        1024 维向量一并序列化回传，而 service 层随即丢弃。``r`` 保持全量：它的属性
        会整体进入响应的 ``edges[].properties``。

        注意：map projection 会把「属性不存在」表示为 ``null``（``properties()``
        则是直接不返回该键），所以 service 层按「值为 null」而非「键是否存在」过滤。
        """

        return await self._query(
            f"""
            MATCH (c:Community {{end_user_id: $end_user_id}})
            MATCH (e:ExtractedEntity {{end_user_id: $end_user_id}})
                  -[b:BELONGS_TO_COMMUNITY]->(c)
            WHERE e.delete_at IS NULL
            OPTIONAL MATCH (e)-[r:EXTRACTED_RELATIONSHIP]-
                           (e2:ExtractedEntity {{end_user_id: $end_user_id}})
            WHERE e2.delete_at IS NULL
            RETURN elementId(c) AS c_id, {_COMMUNITY_NODE_PROJECTION} AS c_props,
                   elementId(e) AS e_id, {_COMMUNITY_ENTITY_PROJECTION} AS e_props,
                   elementId(b) AS b_id, elementId(e2) AS e2_id,
                   {_COMMUNITY_NEIGHBOR_PROJECTION} AS e2_props, elementId(r) AS r_id,
                   properties(r) AS r_props, startNode(r) = e AS r_from_e
            """,
            end_user_id=end_user_id,
        )

    async def community_nodes(self, end_user_id: str) -> list[dict[str, Any]]:
        return await self._query(
            """
            MATCH (e:ExtractedEntity)-[:BELONGS_TO_COMMUNITY]->(c:Community)
            WHERE c.end_user_id = $end_user_id AND e.end_user_id = $end_user_id
              AND e.delete_at IS NULL
            RETURN elementId(c) AS c_id, properties(c) AS c_props,
                   elementId(e) AS e_id, properties(e) AS e_props
            """,
            end_user_id=end_user_id,
        )

    async def community_edges(self, end_user_id: str) -> list[dict[str, Any]]:
        return await self._query(
            """
            MATCH (a)-[r]->(b)
            WHERE a.end_user_id = $end_user_id AND b.end_user_id = $end_user_id
              AND ((a:ExtractedEntity AND b:Community AND type(r) = 'BELONGS_TO_COMMUNITY'
                    AND a.delete_at IS NULL)
                OR (a:ExtractedEntity AND b:ExtractedEntity
                    AND type(r) = 'EXTRACTED_RELATIONSHIP'
                    AND a.delete_at IS NULL AND b.delete_at IS NULL))
            RETURN elementId(r) AS id, type(r) AS type, elementId(a) AS source,
                   elementId(b) AS target, properties(r) AS properties
            """,
            end_user_id=end_user_id,
        )

    async def valid_summary_count(self, end_user_id: str) -> int:
        rows = await self._query(
            """MATCH (n:MemorySummary)-[:DERIVED_FROM_STATEMENT]->(:Statement)
            WHERE n.end_user_id = $end_user_id AND n.delete_at IS NULL
            RETURN count(DISTINCT n) AS count""",
            end_user_id=end_user_id,
        )
        return int(rows[0]["count"] if rows else 0)

    async def episodic_count(self, end_user_id: str) -> int:
        rows = await self._query(
            """MATCH (n:MemorySummary)
            WHERE n.end_user_id=$end_user_id AND n.delete_at IS NULL
            RETURN count(n) AS count""",
            end_user_id=end_user_id,
        )
        return int(rows[0]["count"] if rows else 0)

    async def semantic_explicit_count(self, end_user_id: str) -> int:
        """显性语义记忆数：``is_explicit_memory=true`` 的 ``ExtractedEntity``。

        api ``MemoryBaseService.get_explicit_memory_count`` 的语义侧**不带**软删除
        条件，于是同一个接口里两个加数口径不一致（情景侧 ``episodic_count`` 带、
        语义侧不带），``EXPLICIT_MEMORY`` 会把已遗忘的实体一并算进去。这里与情景侧、
        与 graph_data 各类型查询统一取「未软删除」——**这是对 api 的有意偏离**，
        改回去之前请先确认前端要的是哪套数字。

        代价实测（单用户 608 个实体）：规划器仍走 ``user_extracted_entity(end_user_id)``
        索引，``delete_at`` 由 Filter 逐行判定，DB accesses 1217 → 1275（+58），
        索引项数不变，只是结果从 58 收敛到真实未删除的 1 个。
        """

        rows = await self._query(
            """MATCH (n:ExtractedEntity)
            WHERE n.end_user_id=$end_user_id AND n.delete_at IS NULL
              AND n.is_explicit_memory=true
            RETURN count(n) AS count""",
            end_user_id=end_user_id,
        )
        return int(rows[0]["count"] if rows else 0)

    async def emotional_count(self, end_user_id: str) -> int:
        """情绪记忆数：只统计归一化后落在 10 桶内的情绪。

        api 侧由 ``EmotionRepository.get_emotion_tags`` 按原始值分组、再由
        ``EmotionAnalyticsService`` 归并到 10 桶后求和，无法归一化的脏值被丢弃；
        这里把同一规则下推到 Cypher，省掉一次分组往返。
        """
        rows = await self._query(
            """MATCH (n:Statement)
            WHERE n.end_user_id=$end_user_id AND n.delete_at IS NULL
              AND n.emotion_type IS NOT NULL
              AND toLower(trim(n.emotion_type)) IN $emotion_types
            RETURN count(n) AS count""",
            end_user_id=end_user_id,
            emotion_types=list(EMOTION_TYPE_STORAGE_VALUES),
        )
        return int(rows[0]["count"] if rows else 0)

    async def workspace_implicit_count(
        self,
        end_user_ids: list[str],
        minimum_summary_count: int,
    ) -> int:
        """工作空间口径的隐性记忆：按用户统计，不足阈值该用户贡献 0。"""

        if not end_user_ids:
            return 0
        rows = await self._query(
            _WORKSPACE_IMPLICIT_MEMORY_QUERY,
            end_user_ids=list(end_user_ids),
            minimum_summary_count=minimum_summary_count,
        )
        return int(rows[0].get("implicit_count") or 0) if rows else 0

    async def workspace_fallback_statistics(
        self,
        end_user_ids: list[str],
    ) -> dict[str, int]:
        """ES 中完全无数据的用户回退到 Neo4j 统计的三类计数。

        回退条件与 api 一致：用户在三个相关 ES 索引里**都不存在**有效数据时才回退，
        因此这里的 ``emotion_type IS NOT NULL`` 与 ES 侧的 ``exists`` 语义对齐，
        不做 10 桶归一化（归一化只在 ``emotional_count`` 的单用户展示口径里使用）。
        """

        if not end_user_ids:
            return {
                "episodic_count": 0,
                "explicit_entity_count": 0,
                "emotional_count": 0,
            }
        rows = await self._query(
            _WORKSPACE_FALLBACK_STATISTICS_QUERY,
            end_user_ids=list(end_user_ids),
        )
        row = rows[0] if rows else {}
        return {
            "episodic_count": int(row.get("episodic_count") or 0),
            "explicit_entity_count": int(row.get("explicit_entity_count") or 0),
            "emotional_count": int(row.get("emotional_count") or 0),
        }
