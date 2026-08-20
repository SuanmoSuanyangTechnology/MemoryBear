import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.models.service_models import ForgetLog, MemoryContext
from app.core.memory.storage_services.forgetting_engine.constants import (
    AUXILIARY_MAX_PER_RUN,
    DIALOGUE_AUDIT_CONTENT_MAX_LENGTH,
    FORGET_CORE_BATCH_SIZE,
)
from app.core.utils.datetime_utils import to_iso_z, to_timestamp_ms, utcnow
from app.db import get_db_context
from app.models.memory_forget_model import ForgetTrigger
from app.repositories.forget_log_repository import ForgetLogRepository
from app.repositories.neo4j.graph_search import (
    forget_count_active_nodes,
    forget_count_auxiliary_active_nodes,
    forget_get_auxiliary_candidates,
    forget_get_core_candidates,
    forget_soft_delete_by_element_ids,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.utils.redis_cache import invalidate_cache

logger = logging.getLogger(__name__)


class ForgetService:
    """执行一次基于记忆价值的双池遗忘。

    核心池负责释放配额，辅助池按照核心池的实际释放比例清理
    ``MemorySummary`` 和 ``Dialogue``。所有删除均为软删除并写入审计日志。
    """

    BATCH_SIZE = FORGET_CORE_BATCH_SIZE
    ENTITY_PROTECTION_THRESHOLD = 10

    def __init__(
        self,
        ctx: MemoryContext,
        memory_limit: int,
        *,
        evaluated_at: datetime | None = None,
    ) -> None:
        """初始化本轮遗忘所需的固定配置和时间边界。

        Args:
            ctx: 当前终端用户及其生效中的记忆配置。
            memory_limit: 核心三类节点的配额上限。
            evaluated_at: 本轮统一评估时间；未传入时使用当前 UTC 时间。
        """
        self.ctx = ctx
        self.trigger_count = memory_limit
        self.evaluated_at = evaluated_at or utcnow()
        self.evaluated_at_ms = to_timestamp_ms(self.evaluated_at)

        config = self.ctx.memory_config
        lambda_mem = float(getattr(config, "lambda_mem", 0.5))
        self.target_ratio = 1 - lambda_mem
        self.target_count = max(int(memory_limit * self.target_ratio), 50)

        self._connector: Neo4jConnector | None = None
        self._scanned_count = 0
        self._released_count = 0
        self._node_type_counts: defaultdict[str, int] = defaultdict(int)
        self._core_candidate_query_empty = False
        self._auxiliary_released_count = 0
        self._auxiliary_node_type_counts: defaultdict[str, int] = defaultdict(int)

    async def run(self) -> dict[str, Any]:
        """执行核心池和辅助池遗忘并返回本轮删除摘要。

        核心池未超过配额时直接返回；超过配额时先按价值从低到高删除，
        再根据核心池实际释放比例计算辅助池预算。核心池无候选时返回
        ``no_candidate=True``，由上层写入冷却键。
        """
        self._reset_run_state()
        async with Neo4jConnector() as connector:
            self._connector = connector
            active_count = await forget_count_active_nodes(
                connector, self.ctx.end_user_id
            )
            summary = self._initial_summary(active_count)

            if active_count <= self.trigger_count:
                logger.info(
                    "ForgetService skip — active=%d <= trigger=%d",
                    active_count,
                    self.trigger_count,
                )
                summary.update(self._empty_result(active_count))
                return summary

            budget = active_count - self.target_count
            logger.info(
                "ForgetService start — active=%d trigger=%d target=%d budget=%d "
                "batch_size=%d",
                active_count,
                self.trigger_count,
                self.target_count,
                budget,
                self.BATCH_SIZE,
            )

            budget = await self._mixed_clean(budget)

            auxiliary_active_count = await forget_count_auxiliary_active_nodes(
                connector, self.ctx.end_user_id
            )
            release_ratio = (
                self._released_count / active_count
                if active_count and self._released_count
                else 0.0
            )
            auxiliary_budget = min(
                math.ceil(auxiliary_active_count * release_ratio),
                self._released_count,
                AUXILIARY_MAX_PER_RUN,
            )
            auxiliary_remaining_budget = auxiliary_budget
            if auxiliary_budget > 0:
                auxiliary_remaining_budget = await self._auxiliary_clean(
                    auxiliary_budget
                )

            final_count = await forget_count_active_nodes(
                connector, self.ctx.end_user_id
            )
            auxiliary_final_count = await forget_count_auxiliary_active_nodes(
                connector, self.ctx.end_user_id
            )
            net_active_change = active_count - final_count
            no_candidate = (
                self._released_count == 0 and self._core_candidate_query_empty
            )

            summary.update(
                {
                    "deleted": self._released_count,
                    "scanned_count": self._scanned_count,
                    "node_type_counts": dict(self._node_type_counts),
                    "net_active_change": net_active_change,
                    "budget": max(budget, 0),
                    "final_count": final_count,
                    "no_candidate": no_candidate,
                    "forget_no_candidate_runs": int(no_candidate),
                    "auxiliary_active_count": auxiliary_active_count,
                    "auxiliary_budget": auxiliary_budget,
                    "auxiliary_deleted": self._auxiliary_released_count,
                    "auxiliary_remaining_budget": max(
                        auxiliary_remaining_budget, 0
                    ),
                    "auxiliary_final_count": auxiliary_final_count,
                    "auxiliary_node_type_counts": dict(
                        self._auxiliary_node_type_counts
                    ),
                    "release_ratio": release_ratio,
                }
            )

            logger.info(
                "ForgetService done — scanned=%d deleted=%d auxiliary_deleted=%d "
                "net_active_change=%d final=%d remaining_budget=%d "
                "no_candidate=%s forget_no_candidate_runs=%d",
                self._scanned_count,
                self._released_count,
                self._auxiliary_released_count,
                net_active_change,
                final_count,
                summary["budget"],
                no_candidate,
                summary["forget_no_candidate_runs"],
            )
            await self._invalidate_caches()
            return summary

    def _reset_run_state(self) -> None:
        """清空实例复用时可能残留的本轮计数状态。"""
        self._scanned_count = 0
        self._released_count = 0
        self._node_type_counts = defaultdict(int)
        self._core_candidate_query_empty = False
        self._auxiliary_released_count = 0
        self._auxiliary_node_type_counts = defaultdict(int)

    def _initial_summary(self, active_count: int) -> dict[str, Any]:
        """构造与旧遗忘流程兼容的基础执行摘要。"""
        return {
            "end_user_id": self.ctx.end_user_id,
            "trigger": self.trigger_count,
            "target": self.target_count,
            "protection_threshold": self.ENTITY_PROTECTION_THRESHOLD,
            "initial_count": active_count,
        }

    @staticmethod
    def _empty_result(active_count: int) -> dict[str, Any]:
        """返回无需执行遗忘时的完整空结果。"""
        return {
            "deleted": 0,
            "scanned_count": 0,
            "node_type_counts": {},
            "net_active_change": 0,
            "budget": 0,
            "final_count": active_count,
            "no_candidate": False,
            "forget_no_candidate_runs": 0,
            "auxiliary_active_count": 0,
            "auxiliary_budget": 0,
            "auxiliary_deleted": 0,
            "auxiliary_remaining_budget": 0,
            "auxiliary_final_count": 0,
            "auxiliary_node_type_counts": {},
            "release_ratio": 0.0,
        }

    async def _mixed_clean(self, budget: int) -> int:
        """按记忆价值从低到高清理核心池。

        Args:
            budget: 本轮最多释放的核心节点数。

        Returns:
            清理后的核心池剩余预算。
        """
        if budget <= 0:
            return budget
        connector = self._connector
        if connector is None:
            raise RuntimeError("ForgetService connector is not initialized")

        total_deleted = 0
        audit: list[ForgetLog] = []
        while budget > 0:
            batch_size = min(self.BATCH_SIZE, budget)
            candidates = await forget_get_core_candidates(
                connector,
                self.ctx.end_user_id,
                batch_size,
                self.ENTITY_PROTECTION_THRESHOLD,
                self.evaluated_at_ms,
            )
            if not candidates:
                self._core_candidate_query_empty = True
                break

            element_ids = [row["element_id"] for row in candidates]
            now = utcnow()
            self._scanned_count += len(candidates)

            deleted_element_ids = await forget_soft_delete_by_element_ids(
                connector,
                self.ctx.end_user_id,
                element_ids,
                to_iso_z(now),
            )
            deleted_id_set = set(deleted_element_ids)
            deleted_rows = [
                row for row in candidates if row["element_id"] in deleted_id_set
            ]
            deleted_in_batch = len(deleted_rows)
            total_deleted += deleted_in_batch
            self._released_count += deleted_in_batch
            budget -= deleted_in_batch

            for row in deleted_rows:
                node_type = row.get("node_type", "unknown")
                self._node_type_counts[node_type] += 1
                audit.append(self._build_audit(row, now))

            logger.info(
                "ForgetService core: batch=%d deleted=%d/%d remaining_budget=%d "
                "types=%s",
                len(element_ids),
                deleted_in_batch,
                total_deleted,
                budget,
                {
                    node_type.value: sum(
                        1
                        for row in deleted_rows
                        if row.get("node_type") == node_type.value
                    )
                    for node_type in (
                        Neo4jNodeType.CHUNK,
                        Neo4jNodeType.STATEMENT,
                        Neo4jNodeType.EXTRACTEDENTITY,
                    )
                },
            )
            if deleted_in_batch < len(element_ids):
                break

        self._sync_audit(audit)
        active_count = await forget_count_active_nodes(
            connector, self.ctx.end_user_id
        )
        new_budget = max(0, active_count - self.target_count)
        logger.info(
            "ForgetService core done: total_deleted=%d budget_after=%d",
            total_deleted,
            new_budget,
        )
        return new_budget

    async def _auxiliary_clean(self, budget: int) -> int:
        """按创建时间从旧到新清理辅助池。

        Args:
            budget: 根据核心池实际释放比例计算出的辅助池预算。

        Returns:
            尚未使用的辅助池预算。
        """
        if budget <= 0:
            return budget
        connector = self._connector
        if connector is None:
            raise RuntimeError("ForgetService connector is not initialized")

        audit: list[ForgetLog] = []
        while budget > 0:
            batch_size = min(self.BATCH_SIZE, budget)
            candidates = await forget_get_auxiliary_candidates(
                connector,
                self.ctx.end_user_id,
                batch_size,
                DIALOGUE_AUDIT_CONTENT_MAX_LENGTH,
            )
            if not candidates:
                break

            element_ids = [row["element_id"] for row in candidates]
            now = utcnow()
            deleted_element_ids = await forget_soft_delete_by_element_ids(
                connector,
                self.ctx.end_user_id,
                element_ids,
                to_iso_z(now),
            )
            deleted_id_set = set(deleted_element_ids)
            deleted_rows = [
                row for row in candidates if row["element_id"] in deleted_id_set
            ]
            deleted_in_batch = len(deleted_rows)
            self._auxiliary_released_count += deleted_in_batch
            budget -= deleted_in_batch

            for row in deleted_rows:
                node_type = row.get("node_type", "unknown")
                self._auxiliary_node_type_counts[node_type] += 1
                audit.append(self._build_audit(row, now))
            logger.info(
                "ForgetService auxiliary: batch=%d deleted=%d remaining_budget=%d "
                "types=%s",
                len(element_ids),
                deleted_in_batch,
                budget,
                dict(self._auxiliary_node_type_counts),
            )
            if deleted_in_batch < len(element_ids):
                break

        self._sync_audit(audit)
        return budget

    def _build_audit(self, row: dict[str, Any], now: datetime) -> ForgetLog:
        """把一个实际软删除的 Neo4j 节点转换为 PostgreSQL 审计记录。"""
        return ForgetLog(
            node_id=row["element_id"],
            node_type=row.get("node_type", "unknown"),
            end_user_id=uuid.UUID(self.ctx.end_user_id),
            reason="timeout",
            recoverable=True,
            operator=None,
            delete_at=now,
            trigger=ForgetTrigger.Scheduled.value,
            content=row.get("content") or "",
        )

    def _sync_audit(self, logs: list[ForgetLog]) -> None:
        """同步写入本批审计日志；失败时让任务失败以暴露不一致。"""
        if not logs:
            return
        try:
            with get_db_context() as db:
                ForgetLogRepository.sync_logs(db, logs)
                db.commit()
        except Exception:
            logger.error(
                "forget_audit_write_failed=1 end_user_id=%s log_count=%d",
                self.ctx.end_user_id,
                len(logs),
                exc_info=True,
            )
            raise

    async def _invalidate_caches(self) -> None:
        """删除遗忘预览和配额明细缓存，确保后续读取看到最新结果。"""
        try:
            uid = self.ctx.end_user_id
            await invalidate_cache(prefix=f"forget_candidates:{uid}")
            await invalidate_cache(prefix=f"quota_breakdown:{uid}")
        except Exception:
            logger.warning("Failed to invalidate forget cache", exc_info=True)
