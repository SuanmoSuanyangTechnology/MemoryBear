"""Tenant-filtered Neo4j analytics queries."""

from __future__ import annotations

from typing import Any

from src.infrastructure.neo4j import Neo4jManager

SUPPORTED_NODE_TYPES = frozenset(
    {
        "Dialogue",
        "Chunk",
        "AssistantOriginal",
        "AssistantPruned",
        "Conversation",
        "Statement",
        "ExtractedEntity",
        "MemorySummary",
        "Perceptual",
    }
)
DEFAULT_PER_TYPE_LIMITS = {
    "Dialogue": 20,
    "Chunk": 30,
    "AssistantOriginal": 20,
    "AssistantPruned": 20,
    "Conversation": 20,
    "Statement": 50,
    "ExtractedEntity": 50,
    "MemorySummary": 20,
    "Perceptual": 20,
}
NODE_PROPERTY_WHITELIST = {
    "Dialogue": ("content", "created_at"),
    "Chunk": ("content", "created_at"),
    "Statement": (
        "temporal_info",
        "stmt_type",
        "statement",
        "valid_at",
        "created_at",
        "caption",
        "emotion_keywords",
        "emotion_type",
        "emotion_subject",
    ),
    "ExtractedEntity": (
        "description",
        "description_summary",
        "name",
        "entity_type",
        "created_at",
        "caption",
        "aliases",
        "connect_strength",
    ),
    "MemorySummary": ("summary", "content", "created_at", "caption"),
    "Perceptual": ("file_name", "file_path", "file_type", "domain", "topic", "keywords", "summary"),
    "AssistantOriginal": ("text", "created_at"),
    "AssistantPruned": ("text", "memory_type", "created_at"),
    "Conversation": ("conversation_id", "name", "created_at"),
    "Community": (
        "community_id",
        "end_user_id",
        "member_count",
        "updated_at",
        "name",
        "summary",
        "core_entities",
    ),
}

# ``_properties()`` 在 label 未登记时只保留 caption，投影必须使用同一回落值。
WHITELIST_DEFAULT_FIELDS: tuple[str, ...] = ("caption",)


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


# Center_Mode 单次查询会返回混合 label 的邻居，无法按 label 下发投影，故取全部
# 白名单字段的并集：不属于某节点 label 的字段取到 null，由 service 层丢弃。
_UNION_PROJECTION = ", ".join(
    f"{field}: n.{field}"
    for field in sorted({field for fields in NODE_PROPERTY_WHITELIST.values() for field in fields})
)


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
        query = f"""
            MATCH (n:`{node_type}`)
            WHERE n.end_user_id = $end_user_id AND n.delete_at IS NULL
            OPTIONAL MATCH (n)-[rel]-(other)
            WHERE other.end_user_id = $end_user_id
            RETURN elementId(n) AS id, labels(n)[0] AS label,
                   {{{whitelist_projection(node_type)}}} AS properties,
                   count(rel) AS rel_count
            ORDER BY elementId(n) LIMIT $limit
        """
        return await self._query(query, end_user_id=end_user_id, limit=limit)

    async def centered_nodes(
        self, end_user_id: str, center_node_id: str, depth: int, limit: int
    ) -> list[dict[str, Any]]:
        query = f"""
            MATCH (center) WHERE elementId(center) = $center_node_id
              AND center.end_user_id = $end_user_id AND center.delete_at IS NULL
            OPTIONAL MATCH path=(center)-[*1..{depth}]-(neighbor)
            WHERE neighbor.end_user_id = $end_user_id AND neighbor.delete_at IS NULL
            WITH [center] + collect(DISTINCT neighbor) AS candidates
            UNWIND candidates AS n
            WITH DISTINCT n LIMIT $limit
            OPTIONAL MATCH (n)-[rel]-(other)
            WHERE other.end_user_id = $end_user_id
            RETURN elementId(n) AS id, labels(n)[0] AS label,
                   {{{_UNION_PROJECTION}}} AS properties,
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
            RETURN elementId(r) AS id, type(r) AS type,
                   elementId(a) AS source, elementId(b) AS target, properties(r) AS properties
            """,
            end_user_id=end_user_id,
            node_ids=node_ids,
        )

    async def community_graph_rows(self, end_user_id: str) -> list[dict[str, Any]]:
        return await self._query(
            """
            MATCH (c:Community {end_user_id: $end_user_id})
            MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
                  -[b:BELONGS_TO_COMMUNITY]->(c)
            WHERE e.delete_at IS NULL
            OPTIONAL MATCH (e)-[r:EXTRACTED_RELATIONSHIP]-
                           (e2:ExtractedEntity {end_user_id: $end_user_id})
            WHERE e2.delete_at IS NULL
            RETURN elementId(c) AS c_id, properties(c) AS c_props,
                   elementId(e) AS e_id, properties(e) AS e_props,
                   elementId(b) AS b_id, elementId(e2) AS e2_id,
                   properties(e2) AS e2_props, elementId(r) AS r_id,
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
        rows = await self._query(
            """MATCH (n:ExtractedEntity)
            WHERE n.end_user_id=$end_user_id AND n.is_explicit_memory=true
            RETURN count(n) AS count""",
            end_user_id=end_user_id,
        )
        return int(rows[0]["count"] if rows else 0)

    async def emotional_count(self, end_user_id: str) -> int:
        rows = await self._query(
            """MATCH (n:Statement)
            WHERE n.end_user_id=$end_user_id AND n.emotion_type IS NOT NULL
              AND n.delete_at IS NULL
            RETURN count(n) AS count""",
            end_user_id=end_user_id,
        )
        return int(rows[0]["count"] if rows else 0)
