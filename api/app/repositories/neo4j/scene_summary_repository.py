"""SceneSummary data access for implicit-memory profile generation."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.repositories.neo4j.neo4j_connector import Neo4jConnector


class SceneSummaryRepository:
    """Reads valid SceneSummary nodes for a single end user."""

    def __init__(self, connector: Neo4jConnector):
        self.connector = connector

    async def find_by_end_user_id(
        self,
        end_user_id: str,
        limit: int = 1000,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Return non-empty SceneSummary nodes ordered by scene completion time."""
        query = """
        MATCH (scene:SceneSummary {end_user_id: $end_user_id})
        WHERE trim(coalesce(scene.content, '')) <> ''
        """
        params: Dict[str, Any] = {"end_user_id": end_user_id, "limit": limit}

        if start_date:
            query += " AND coalesce(scene.ended_at, scene.created_at) >= $start_date"
            params["start_date"] = start_date

        if end_date:
            query += " AND coalesce(scene.ended_at, scene.created_at) <= $end_date"
            params["end_date"] = end_date

        query += """
        RETURN properties(scene) AS scene
        ORDER BY coalesce(scene.ended_at, scene.created_at) DESC, scene.id ASC
        LIMIT $limit
        """
        results = await self.connector.execute_query(query, **params)
        return [dict(result["scene"]) for result in results]
