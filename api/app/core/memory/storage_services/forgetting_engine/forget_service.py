import logging
from typing import Any

from app.core.memory.models.service_models import MemoryContext
from app.core.utils.datetime_utils import utcnow, to_iso_z
from app.repositories.neo4j.graph_search import (
    forget_count_active_nodes,
    forget_get_mixed_candidates,
    forget_soft_delete_by_element_ids,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


class ForgetService:
    BATCH_SIZE = 50
    ENTITY_PROTECTION_THRESHOLD = 10

    def __init__(self, ctx: MemoryContext, memory_limit: int) -> None:
        self.ctx = ctx
        self.trigger_count = memory_limit
        self._connector: Neo4jConnector | None = None
        self._audit: list[dict[str, Any]] = []
        self.target_ratio = (1 - self.ctx.memory_config.lambda_mem)
        self.target_count = max(int(memory_limit * self.target_ratio), 50)

    async def run(self) -> dict[str, Any]:
        self._audit = []
        async with Neo4jConnector() as connector:
            self._connector = connector

            active_count = await forget_count_active_nodes(connector, self.ctx.end_user_id)
            summary = {
                "end_user_id": self.ctx.end_user_id,
                "trigger": self.trigger_count,
                "target": self.target_count,
                "protection_threshold": self.ENTITY_PROTECTION_THRESHOLD,
                "initial_count": active_count,
            }

            if active_count <= self.trigger_count:
                logger.info(
                    "ForgetService skip — active=%d <= trigger=%d",
                    active_count, self.trigger_count,
                )
                summary["deleted"] = 0
                summary["final_count"] = active_count
                return summary

            budget = active_count - self.target_count
            logger.info(
                "ForgetService start — active=%d trigger=%d target=%d budget=%d "
                "protection_threshold=%d",
                active_count, self.trigger_count, self.target_count, budget,
                self.ENTITY_PROTECTION_THRESHOLD,
            )

            budget = await self._mixed_clean(budget)

            final_count = await forget_count_active_nodes(connector, self.ctx.end_user_id)
            deleted = summary["initial_count"] - final_count

            summary["deleted"] = deleted
            summary["budget"] = max(budget, 0)
            summary["final_count"] = final_count

            logger.info(
                "ForgetService done — deleted=%d final=%d remaining_budget=%d",
                deleted, final_count, summary["budget"],
            )
            return summary

    async def _mixed_clean(self, budget: int) -> int:
        if budget <= 0:
            return budget

        connector = self._connector
        total_deleted = 0

        while budget > 0:
            batch_size = min(self.BATCH_SIZE, budget)
            candidates = await forget_get_mixed_candidates(
                connector, self.ctx.end_user_id, batch_size,
                self.ENTITY_PROTECTION_THRESHOLD,
            )

            if not candidates:
                break

            element_ids = [row["element_id"] for row in candidates]
            now = utcnow()

            deleted_in_batch = await forget_soft_delete_by_element_ids(
                connector, self.ctx.end_user_id, element_ids, to_iso_z(now),
            )

            total_deleted += deleted_in_batch
            budget -= deleted_in_batch

            for row in candidates:
                node_type = row.get("node_type", "unknown")
                entry: dict[str, Any] = {
                    "node_id": row.get("node_id"),
                    "node_type": node_type,
                    "user_id": self.ctx.end_user_id,
                    "delete_at": now,
                    "delete_reason": "mixed_time_asc",
                    "sort_time": row.get("sort_time"),
                    "extraction_count": row.get("extraction_count"),
                }
                self._audit.append(entry)

            logger.info(
                "ForgetService mixed: batch=%d deleted=%d/%d remaining_budget=%d "
                "types=%s",
                len(element_ids), deleted_in_batch, total_deleted, budget,
                {t: sum(1 for r in candidates if r.get("node_type") == t)
                 for t in ("chunk", "statement", "entity")},
            )

            if deleted_in_batch < len(element_ids):
                break

        active_count = await forget_count_active_nodes(connector, self.ctx.end_user_id)
        new_budget = max(0, active_count - self.target_count)
        logger.info(
            "ForgetService mixed done: total_deleted=%d budget_before=%d budget_after=%d",
            total_deleted, budget + total_deleted, new_budget,
        )
        return new_budget
