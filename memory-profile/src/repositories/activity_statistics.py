"""Read-only workspace-scoped display event aggregates."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Date, and_, case, cast, func, literal, or_, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import (
    EndUser,
    EndUserMerge,
    MemoryDisplayRecord,
    MemoryEngineDisplayEvent,
)

OTHER_ENGINE_TYPES = (
    "CROSS_MODAL",
    "EMOTION",
    "SCENE_SUMMARY",
    "MEMORY_VALUE",
    "PREFERENCE",
    "SCENE_RECONSTRUCTION",
    "PREDICTION",
)


class ActivityStatisticsRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def counts(
        self,
        workspace_id: UUID,
        zone_name: str,
        start: datetime,
        end: datetime,
        include_users: bool,
    ) -> tuple[dict, set[UUID]]:
        counts: dict = {}
        user_ids: set[UUID] = set()
        if end <= start:
            return counts, user_ids

        async with self.postgres.session() as session:
            for model in (MemoryDisplayRecord, MemoryEngineDisplayEvent):
                if model is MemoryDisplayRecord:
                    category_expr = literal("retrieval")
                    type_filter = model.operation == "RETRIEVE"
                else:
                    category_expr = case(
                        (model.engine_type == "EXTRACTION", "extraction"),
                        (model.engine_type == "REFLECTION", "reflection"),
                        (model.engine_type == "FORGETTING", "forgetting"),
                        else_="other",
                    )
                    type_filter = model.engine_type.in_(
                        ("EXTRACTION", "REFLECTION", "FORGETTING", *OTHER_ENGINE_TYPES)
                    )
                local_day = cast(
                    func.timezone(zone_name, func.timezone("UTC", model.occurred_at)), Date
                )
                dimensions = [local_day, category_expr]
                if include_users:
                    dimensions.append(model.end_user_id)
                statement = (
                    select(*dimensions, func.count())
                    .select_from(model)
                    .outerjoin(
                        EndUser, and_(model.workspace_id.is_(None), EndUser.id == model.end_user_id)
                    )
                    .where(
                        or_(
                            model.workspace_id == workspace_id,
                            and_(
                                model.workspace_id.is_(None), EndUser.workspace_id == workspace_id
                            ),
                        ),
                        model.occurred_at >= start.replace(tzinfo=None),
                        model.occurred_at < end.replace(tzinfo=None),
                        type_filter,
                    )
                    .group_by(*dimensions)
                )
                for row in (await session.execute(statement)).all():
                    day, kind = row[0], row[1]
                    counts.setdefault(
                        day,
                        {
                            key: 0
                            for key in (
                                "extraction",
                                "retrieval",
                                "reflection",
                                "forgetting",
                                "other",
                            )
                        },
                    )[kind] += row[-1]
                    if include_users and row[2] is not None:
                        user_ids.update((row[2],))
        return counts, user_ids

    async def involved_users(self, workspace_id: UUID, user_ids: set[UUID]) -> int:
        if not user_ids:
            return 0
        async with self.postgres.session() as session:
            mappings = (
                await session.execute(
                    select(EndUserMerge.origin_id, EndUserMerge.target_id).where(
                        EndUserMerge.workspace_id == workspace_id,
                        EndUserMerge.origin_id.is_not(None),
                    )
                )
            ).all()
        targets = {origin: target for origin, target in mappings}

        def final_target(user_id: UUID) -> UUID:
            seen: set[UUID] = set()
            while user_id in targets and user_id not in seen:
                seen.update((user_id,))
                user_id = targets[user_id]
            return user_id

        return len({final_target(user_id) for user_id in user_ids})
