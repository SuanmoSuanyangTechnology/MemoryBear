"""Update a Statement and produce the UPSERT consumed by the existing outbox worker."""

from uuid import uuid4

from sqlalchemy import UUID, DateTime, String, column, insert, table

from src.common.timing import utcnow_naive
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager

# Map only the columns written by this producer. The API owns the table and its migration.
_OUTBOX = table(
    "memory_storage_outbox_events",
    column("id", UUID),
    column("label", String),
    column("node_id", String),
    column("operation", String),
    column("created_at", DateTime),
    column("updated_at", DateTime),
)

_UNMARK = """
MATCH (s:Statement {end_user_id: $end_user_id})
WHERE elementId(s) = $element_id AND s.delete_at IS NULL
  AND s.id IS NOT NULL AND trim(toString(s.id)) <> ''
SET s.is_permanent = false
RETURN toString(s.id) AS node_id
"""


class ValueRankingMutation:
    def __init__(self, postgres: PostgresManager, neo4j: Neo4jManager) -> None:
        self.postgres = postgres
        self.neo4j = neo4j

    async def unmark(self, element_id: str, end_user_id: str) -> bool:
        driver = await self.neo4j.driver()
        records, _, _ = await driver.execute_query(
            _UNMARK,
            database_=self.neo4j.database,
            element_id=element_id,
            end_user_id=end_user_id,
        )
        if not records:
            return False

        # Enqueue after the graph commit, as the API storage writer does.
        # Repeating an unmark can enqueue again if the previous enqueue failed.
        now = utcnow_naive()
        async with self.postgres.session() as session, session.begin():
            await session.execute(
                insert(_OUTBOX).values(
                    id=uuid4(),
                    label="Statement",
                    node_id=records[0]["node_id"],
                    operation="upsert",
                    created_at=now,
                    updated_at=now,
                )
            )
        return True
