"""Parameterized, bounded Neo4j reads for scene communities and summaries."""

from uuid import UUID

from neo4j import Query

from src.infrastructure.neo4j import Neo4jManager
from src.schemas.scene_community_graph import COMMUNITY_FIELDS, SCENE_FIELDS


def _query(label: str, variable: str, fields: tuple[str, ...]) -> str:
    # label/fields only come from constants, never from a request parameter.
    projection = ", ".join(f".{field}" for field in fields)
    return (
        f"MATCH ({variable}:{label} {{end_user_id: $end_user_id}}) "
        f"RETURN {variable}.id AS id, {variable} {{{projection}}} AS properties "
        "LIMIT $probe_limit"
    )


COMMUNITIES_QUERY = _query("SceneCommunity", "c", COMMUNITY_FIELDS)
SCENES_QUERY = _query("SceneSummary", "s", SCENE_FIELDS)


class Neo4jRepository:
    def __init__(self, neo4j: Neo4jManager, transaction_timeout: float = 3.0):
        self.neo4j = neo4j
        self.transaction_timeout = transaction_timeout

    async def _read(self, query: str, end_user_id: UUID, limit: int) -> list[dict]:
        return await self.neo4j.read_query(
            Query(query, timeout=self.transaction_timeout),
            end_user_id=str(end_user_id),
            probe_limit=limit,
        )

    async def communities(self, end_user_id: UUID, limit: int) -> list[dict]:
        return await self._read(COMMUNITIES_QUERY, end_user_id, limit)

    async def scenes(self, end_user_id: UUID, limit: int) -> list[dict]:
        return await self._read(SCENES_QUERY, end_user_id, limit)
