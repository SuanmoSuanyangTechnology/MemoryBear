"""SceneSummary and MemorySummary access for implicit-memory generation."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.repositories.neo4j.neo4j_connector import Neo4jConnector


class ImplicitMemorySummaryRepository:
    """Reads both summary types used to build an end user's implicit profile."""

    def __init__(self, connector: Neo4jConnector):
        self.connector = connector

    async def count_by_end_user_id(self, end_user_id: str) -> int:
        """Count non-empty summaries without fetching their content."""
        results = await self.connector.execute_query(
            """
            MATCH (scene)
            WHERE (scene:SceneSummary OR scene:MemorySummary)
              AND scene.end_user_id = $end_user_id
              AND trim(coalesce(scene.content, '')) <> ''
            RETURN count(scene) AS count
            """,
            end_user_id=end_user_id,
        )
        return int(results[0]["count"]) if results else 0

    async def find_by_end_user_id(
        self,
        end_user_id: str,
        limit: int = 1000,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Return both non-empty summary types in a single limited result set."""
        query = """
        MATCH (scene)
        WHERE (scene:SceneSummary OR scene:MemorySummary)
          AND scene.end_user_id = $end_user_id
          AND trim(coalesce(scene.content, '')) <> ''
        """
        params: Dict[str, Any] = {"end_user_id": end_user_id, "limit": limit}

        if start_date:
            query += " AND coalesce(scene.ended_at, scene.created_at) >= $start_date"
            params["start_date"] = start_date

        if end_date:
            query += " AND coalesce(scene.ended_at, scene.created_at) <= $end_date"
            params["end_date"] = end_date

        query += """
        RETURN properties(scene) AS scene,
               CASE WHEN scene:SceneSummary THEN 'scene_summary'
                    ELSE 'memory_summary' END AS summary_type
        ORDER BY coalesce(scene.ended_at, scene.created_at) DESC, scene.id ASC
        LIMIT $limit
        """
        results = await self.connector.execute_query(query, **params)
        return [
            {**dict(result["scene"]), "summary_type": result["summary_type"]}
            for result in results
        ]
