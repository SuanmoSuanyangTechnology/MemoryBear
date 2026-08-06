import logging
import uuid
from collections import defaultdict
from typing import Any

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.models.service_models import MemoryContext, ForgetLog
from app.core.utils.datetime_utils import utcnow, to_iso_z
from app.db import get_db_context
from app.models.memory_forget_model import ForgetTrigger
from app.repositories.forget_log_repository import ForgetLogRepository
from app.repositories.neo4j.graph_search import (
    forget_count_active_nodes,
    forget_get_mixed_candidates,
    forget_soft_delete_by_element_ids,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.utils.redis_cache import invalidate_cache

logger = logging.getLogger(__name__)


class ForgetService:
    BATCH_SIZE = 50
    ENTITY_PROTECTION_THRESHOLD = 10

    def __init__(self, ctx: MemoryContext, memory_limit: int) -> None:
        self.ctx = ctx
        self.trigger_count = memory_limit
        self._connector: Neo4jConnector | None = None
        self._audit: list[ForgetLog] = []
        # 展示投影所需的整轮累计量，run() 开头重置，避免实例复用时累计上一轮
        self._scanned_count: int = 0
        self._released_count: int = 0
        self._node_type_counts: defaultdict[str, int] = defaultdict(int)
        self.target_ratio = (1 - self.ctx.memory_config.lambda_mem)
        self.target_count = max(int(memory_limit * self.target_ratio), 50)

    async def run(self) -> dict[str, Any]:
        self._audit = []
        self._scanned_count = 0
        self._released_count = 0
        self._node_type_counts = defaultdict(int)
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
                summary["scanned_count"] = 0
                summary["node_type_counts"] = {}
                summary["net_active_change"] = 0
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
            # 活跃节点净变化只用于运维观察：并发写入或实体归并都会干扰它，
            # 因此不能作为"本轮软删除了多少"的依据。
            net_active_change = summary["initial_count"] - final_count

            # deleted 是逐批 SET delete_at 的实际影响行数之和
            summary["deleted"] = self._released_count
            summary["scanned_count"] = self._scanned_count
            summary["node_type_counts"] = dict(self._node_type_counts)
            summary["net_active_change"] = net_active_change
            summary["budget"] = max(budget, 0)
            summary["final_count"] = final_count

            logger.info(
                "ForgetService done — scanned=%d deleted=%d net_active_change=%d "
                "final=%d remaining_budget=%d",
                self._scanned_count, self._released_count, net_active_change,
                final_count, summary["budget"],
            )
            try:
                uid = self.ctx.end_user_id
                await invalidate_cache(prefix=f"forget_candidates:{uid}")
                await invalidate_cache(prefix=f"quota_breakdown:{uid}")
            except Exception:
                logger.warning("Failed to invalidate forget cache", exc_info=True)
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
            self._scanned_count += len(candidates)

            deleted_in_batch = await forget_soft_delete_by_element_ids(
                connector, self.ctx.end_user_id, element_ids, to_iso_z(now),
            )

            total_deleted += deleted_in_batch
            self._released_count += deleted_in_batch
            budget -= deleted_in_batch

            for row in candidates:
                node_type = row.get("node_type", "unknown")
                self._node_type_counts[node_type] += 1
                entry: ForgetLog = ForgetLog(
                    node_id=row.get("element_id"),
                    node_type=node_type,
                    end_user_id=uuid.UUID(self.ctx.end_user_id),
                    reason="timeout",
                    recoverable=True,
                    operator=None,
                    delete_at=now,
                    trigger=ForgetTrigger.Scheduled.value,
                    content=row.get("content")
                )
                self._audit.append(entry)

            logger.info(
                "ForgetService mixed: batch=%d deleted=%d/%d remaining_budget=%d "
                "types=%s",
                len(element_ids), deleted_in_batch, total_deleted, budget,
                {t: sum(1 for r in candidates if r.get("node_type") == t)
                 for t in (Neo4jNodeType.CHUNK, Neo4jNodeType.STATEMENT, Neo4jNodeType.EXTRACTEDENTITY)},
            )

            if deleted_in_batch < len(element_ids):
                break
        with get_db_context() as db:
            ForgetLogRepository.sync_logs(db, self._audit)
            db.commit()

        active_count = await forget_count_active_nodes(connector, self.ctx.end_user_id)
        new_budget = max(0, active_count - self.target_count)
        logger.info(
            "ForgetService mixed done: total_deleted=%d budget_before=%d budget_after=%d",
            total_deleted, budget + total_deleted, new_budget,
        )
        return new_budget
