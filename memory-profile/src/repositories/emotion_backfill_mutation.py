"""Read repair for the API-owned dialogue emotion projection."""

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from src.common.normalization import as_utc_naive
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.models.references import DialogueEmotionRaw


class EmotionBackfillMutationRepository:
    def __init__(self, postgres: PostgresManager, neo4j: Neo4jManager) -> None:
        self.postgres = postgres
        self.neo4j = neo4j

    async def latest_created_at(self, user_id: UUID) -> datetime | None:
        async with self.postgres.session() as session:
            return await session.scalar(
                select(func.max(DialogueEmotionRaw.created_at)).where(
                    DialogueEmotionRaw.end_user_id == user_id
                )
            )

    async def backfill(self, user_id: UUID, start: datetime, end: datetime) -> int:
        rows = await self.neo4j.read_query(
            """
            MATCH (d:Dialogue)
            WHERE d.end_user_id = $end_user_id
              AND d.emotion IS NOT NULL AND d.created_at IS NOT NULL
              AND d.created_at >= $start_dt AND d.created_at < $end_dt
            RETURN d.id AS id, d.created_at AS created_at, d.emotion AS emotion
            """,
            end_user_id=str(user_id),
            start_dt=start,
            end_dt=end,
        )
        if not rows:
            return 0
        # Use the same IDs and UTC timestamps as the API's scheduled sync.
        values = []
        for row in rows:
            dialogue_id = str(row["id"])
            created_at = row["created_at"]
            if hasattr(created_at, "to_native"):
                created_at = created_at.to_native()
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            values.append(
                {
                    "id": uuid5(NAMESPACE_URL, dialogue_id),
                    "dialogue_id": dialogue_id,
                    "end_user_id": user_id,
                    "created_at": as_utc_naive(created_at),
                    "emotion": row["emotion"],
                }
            )
        async with self.postgres.session() as session:
            for start_index in range(0, len(values), 1000):
                stmt = insert(DialogueEmotionRaw).values(values[start_index : start_index + 1000])
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[DialogueEmotionRaw.id],
                        set_={
                            "end_user_id": stmt.excluded.end_user_id,
                            "created_at": stmt.excluded.created_at,
                            "emotion": stmt.excluded.emotion,
                        },
                    )
                )
            await session.commit()
        return len(values)
