"""Read end-user ownership and the graph data used by value ranking."""

from datetime import datetime
from typing import Any
from uuid import UUID

from neo4j import Record
from sqlalchemy import select

from src.common.normalization import datetime_to_epoch_ms, to_jsonable
from src.common.timing import now_ms
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, Workspace
from src.schemas.value_ranking import PermanentMemoryItem

_COUNT_PERMANENT = """
MATCH (s:Statement {end_user_id: $end_user_id})
WHERE s.delete_at IS NULL AND coalesce(s.is_permanent, false) = true
RETURN count(s) AS used
"""

_COUNT_LIVE = """
MATCH (s:Statement {end_user_id: $end_user_id})
WHERE s.delete_at IS NULL
RETURN count(s) AS total
"""

# Keep scoring aligned with the API Key list. Recency has a 30-day half-life.
_LIST = """
MATCH (s:Statement {end_user_id: $end_user_id})
WHERE s.delete_at IS NULL
WITH s, elementId(s) AS id, toFloat(s.topology_score) AS raw_g,
     CASE
       WHEN s.created_at IS NULL THEN null
       ELSE datetime(s.created_at).epochMillis
     END AS created_epoch
WITH id, s, created_epoch,
     CASE
       WHEN raw_g IS NULL OR isNaN(raw_g) THEN 0.0
       WHEN raw_g < 0.0 THEN 0.0
       WHEN raw_g > 1.0 THEN 1.0
       ELSE raw_g
     END AS g
WITH id, s, g,
     CASE
       WHEN created_epoch IS NULL THEN 0.0
       WHEN created_epoch >= $evaluated_at_ms THEN 1.0
       ELSE 2.0 ^ (- ($evaluated_at_ms - created_epoch) / 2592000000.0)
     END AS t
WITH id, s,
     CASE WHEN coalesce(s.is_permanent, false) THEN 1.0
          ELSE 0.75 * g + 0.25 * t
     END AS value_score
ORDER BY value_score DESC, s.created_at DESC, id ASC
SKIP $skip
LIMIT $limit
RETURN id, 'Statement' AS label,
       {statement: s.statement, created_at: s.created_at,
        is_permanent: coalesce(s.is_permanent, false),
        value_score: sqrt(value_score) * 100} AS properties
"""


class ValueRankingRepository:
    def __init__(self, postgres: PostgresManager, neo4j: Neo4jManager) -> None:
        self.postgres = postgres
        self.neo4j = neo4j

    async def tenant_for_active_user(self, end_user_id: UUID, workspace_id: UUID) -> UUID | None:
        query = (
            select(Workspace.tenant_id)
            .join(EndUser, EndUser.workspace_id == Workspace.id)
            .where(
                EndUser.id == end_user_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
            )
        )
        async with self.postgres.session() as session:
            return await session.scalar(query)

    async def count_permanent(self, end_user_id: str) -> int:
        records = await self._query(_COUNT_PERMANENT, end_user_id=end_user_id)
        return int(records[0]["used"])

    async def count_live(self, end_user_id: str) -> int:
        records = await self._query(_COUNT_LIVE, end_user_id=end_user_id)
        return int(records[0]["total"])

    async def list_memories(
        self, end_user_id: str, page: int, pagesize: int
    ) -> list[PermanentMemoryItem]:
        records = await self._query(
            _LIST,
            end_user_id=end_user_id,
            evaluated_at_ms=now_ms(),
            skip=(page - 1) * pagesize,
            limit=pagesize,
        )
        items = []
        for record in records:
            item = record.data()
            properties = item["properties"]
            created_at = to_jsonable(properties["created_at"])
            try:
                properties["created_at"] = datetime_to_epoch_ms(
                    datetime.fromisoformat(created_at) if isinstance(created_at, str) else None
                )
            except ValueError:
                properties["created_at"] = None
            items.append(PermanentMemoryItem.model_validate(item))
        return items

    async def _query(self, query: str, **parameters: Any) -> list[Record]:
        # Match the legacy writer routing so a list refilled after PATCH sees the update.
        driver = await self.neo4j.driver()
        records, _, _ = await driver.execute_query(
            query, database_=self.neo4j.database, **parameters
        )
        return records
