"""Neo4j repository for idempotent SceneSummary persistence."""

from app.repositories.neo4j.cypher_queries import (
    SCENE_SUMMARY_GET as GET_SCENE_SUMMARY,
    SCENE_SUMMARY_NODE_SAVE as MERGE_SCENE_SUMMARY,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


class SceneSummaryRepository:
    def __init__(self, connector: Neo4jConnector):
        self.connector = connector

    async def get_source_message_ids(self, summary_id: str) -> list[str] | None:
        rows = await self.connector.execute_query(GET_SCENE_SUMMARY, id=summary_id)
        if not rows:
            return None
        return list(rows[0].get("source_message_ids") or [])

    async def upsert(self, summary: dict) -> str:
        rows = await self.connector.execute_query(MERGE_SCENE_SUMMARY, summary=summary)
        if not rows:
            raise RuntimeError("SceneSummary MERGE returned no row")
        return str(rows[0]["id"])
