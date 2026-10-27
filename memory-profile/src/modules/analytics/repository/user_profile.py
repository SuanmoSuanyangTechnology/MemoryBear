"""Neo4j reads behind the legacy admin ``user_profile`` endpoint."""

from __future__ import annotations

from typing import Any

from src.infrastructure.neo4j import Neo4jManager

#: api ``get_user_profile`` 内固定的排除名单（AI/示例用户/占位名）。
HOT_TAG_EXCLUDED_NAMES = [
    "AI",
    "Caroline",
    "Melanie",
    "Jon",
    "Gina",
    "用户",
    "AI助手",
    "John",
    "Maria",
]


class UserProfileRepository:
    """终端用户图谱画像的只读查询。"""

    def __init__(self, neo4j: Neo4jManager) -> None:
        self.neo4j = neo4j

    async def top_entities(
        self,
        end_user_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """按出现次数取用户高频实体（api ``get_user_profile`` 同款 Cypher）。

        ``end_user_id`` 为空时保留 api 的全局语义（``$end_user_id IS NULL`` 分支）。
        """

        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE ($end_user_id IS NULL OR e.end_user_id = $end_user_id) "
            "AND e.entity_type <> '生命体' "
            "AND e.name IS NOT NULL "
            "AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC LIMIT $limit"
        )
        rows = await self.neo4j.read_query(
            query,
            end_user_id=end_user_id,
            names_to_exclude=HOT_TAG_EXCLUDED_NAMES,
            limit=limit,
        )
        return [
            {"name": row.get("name"), "frequency": row.get("frequency")}
            for row in rows
            if row.get("name") is not None
        ]
