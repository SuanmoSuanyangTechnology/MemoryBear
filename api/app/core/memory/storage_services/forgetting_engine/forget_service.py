import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.models.service_models import ForgetLog, MemoryContext
from app.core.memory.storage.custom.automatic_forgetting import (
    AutomaticForgetOutboxError,
    soft_delete_forgetting_nodes,
)
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.memory.storage_services.forgetting_engine.constants import (
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
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.utils.redis_cache import invalidate_cache

logger = logging.getLogger(__name__)


class ForgetService:
    """执行一次由辅助池驱动、核心池配额收敛的软删除遗忘。

    优先删除无有效关系的核心节点；配额不足时按创建时间清理辅助池，
    再删除由此产生的离散核心节点；辅助池耗尽后按 G/T 公式兜底。
    所有删除均为软删除并写入审计日志。
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
        # lambda_mem 表示遗忘比例，目标保留比例为 1 - lambda_mem。
        self.target_ratio = 1 - lambda_mem
        self.target_count = max(int(memory_limit * self.target_ratio), 50)

        self._connector: Neo4jConnector | None = None
        self._storage_client: Neo4jClient | None = None
        self._scanned_count = 0
        self._released_count = 0
        self._node_type_counts: defaultdict[str, int] = defaultdict(int)
        self._core_candidate_query_empty = False
        self._isolated_released_count = 0
        self._fallback_released_count = 0
        self._auxiliary_released_count = 0
        self._auxiliary_candidate_query_empty = False
        self._auxiliary_node_type_counts: defaultdict[str, int] = defaultdict(int)

    async def run(self) -> dict[str, Any]:
        """Execute one automatic forgetting cycle and release storage clients."""
        self._reset_run_state()
        try:
            return await self._run_cycle()
        finally:
            storage_client = self._storage_client
            self._storage_client = None
            if storage_client is not None:
                await storage_client.close()

    async def _run_cycle(self) -> dict[str, Any]:
        """执行离散优先、辅助驱动、时间价值兜底的遗忘。

        核心池未超过配额时直接返回。超过配额时，核心池实际软删除数
        始终受剩余预算约束，避免越过目标水位。
        """
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

            auxiliary_active_count = await forget_count_auxiliary_active_nodes(
                connector, self.ctx.end_user_id
            )

            # 第一优先级：先清理执行前已经完全没有有效关系的核心节点。
            budget = await self._core_clean(
                budget, isolated_only=True, phase="isolated"
            )

            # 第二优先级：辅助池按 created_at 分批软删除；每批之后重新寻找
            # 因辅助端点失效而变成离散状态的核心节点。
            auxiliary_budget = auxiliary_active_count if budget > 0 else 0
            while budget > 0:
                auxiliary_deleted = await self._auxiliary_clean_batch(
                    min(self.BATCH_SIZE, budget)
                )
                if auxiliary_deleted == 0:
                    break
                budget = await self._core_clean(
                    budget, isolated_only=True, phase="isolated"
                )

            # 第三优先级：辅助候选耗尽后，按保留的 G/T 公式清理普通核心
            # 候选直到配额或候选耗尽。目前权重为 0*G + 1*T。
            if budget > 0:
                budget = await self._core_clean(
                    budget, isolated_only=False, phase="fallback"
                )

            final_count = await forget_count_active_nodes(
                connector, self.ctx.end_user_id
            )
            auxiliary_final_count = await forget_count_auxiliary_active_nodes(
                connector, self.ctx.end_user_id
            )
            net_active_change = active_count - final_count
            budget = max(0, final_count - self.target_count)
            release_ratio = (
                self._released_count / active_count
                if active_count and self._released_count
                else 0.0
            )
            no_candidate = (
                budget > 0
                and self._released_count == 0
                and self._core_candidate_query_empty
            )

            summary.update(
                {
                    "deleted": self._released_count,
                    "scanned_count": self._scanned_count,
                    "node_type_counts": dict(self._node_type_counts),
                    "isolated_deleted": self._isolated_released_count,
                    "fallback_deleted": self._fallback_released_count,
                    "net_active_change": net_active_change,
                    "budget": max(budget, 0),
                    "final_count": final_count,
                    "no_candidate": no_candidate,
                    "forget_no_candidate_runs": int(no_candidate),
                    "auxiliary_active_count": auxiliary_active_count,
                    "auxiliary_budget": auxiliary_budget,
                    "auxiliary_deleted": self._auxiliary_released_count,
                    "auxiliary_remaining_budget": max(
                        auxiliary_budget - self._auxiliary_released_count, 0
                    ),
                    "auxiliary_final_count": auxiliary_final_count,
                    "auxiliary_exhausted": self._auxiliary_candidate_query_empty,
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
        self._isolated_released_count = 0
        self._fallback_released_count = 0
        self._auxiliary_released_count = 0
        self._auxiliary_candidate_query_empty = False
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
            "isolated_deleted": 0,
            "fallback_deleted": 0,
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
            "auxiliary_exhausted": False,
            "auxiliary_node_type_counts": {},
            "release_ratio": 0.0,
        }

    async def _get_storage_client(self) -> Neo4jClient:
        if self._storage_client is None:
            self._storage_client = await Neo4jClient.create()
        return self._storage_client

    async def _soft_delete_candidates(
        self,
        candidates: list[dict[str, Any]],
        now: datetime,
        *,
        require_isolated: bool,
    ) -> list[str]:
        """Soft-delete through storage custom and preserve committed audits."""
        try:
            affected_nodes = await soft_delete_forgetting_nodes(
                self.ctx.end_user_id,
                [row["element_id"] for row in candidates],
                to_iso_z(now),
                protection_threshold=self.ENTITY_PROTECTION_THRESHOLD,
                require_isolated=require_isolated,
                client=await self._get_storage_client(),
            )
        except AutomaticForgetOutboxError as exc:
            committed_ids = {node.element_id for node in exc.affected_nodes}
            committed_rows = [
                row for row in candidates if row["element_id"] in committed_ids
            ]
            try:
                self._sync_audit([
                    self._build_audit(row, now) for row in committed_rows
                ])
            except Exception:
                logger.exception(
                    "Failed to persist automatic-forget audit after Outbox "
                    "failure: end_user_id=%s committed=%d",
                    self.ctx.end_user_id,
                    len(committed_rows),
                )
            raise
        return [node.element_id for node in affected_nodes]

    async def _core_clean(
        self,
        budget: int,
        *,
        isolated_only: bool,
        phase: str,
    ) -> int:
        """按阶段清理核心池，并严格限制实际软删除数不超过预算。

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
                isolated_only=isolated_only,
            )
            if not candidates:
                if not isolated_only:
                    self._core_candidate_query_empty = True
                break

            element_ids = [row["element_id"] for row in candidates]
            now = utcnow()
            self._scanned_count += len(candidates)

            deleted_element_ids = await self._soft_delete_candidates(
                candidates,
                now,
                require_isolated=isolated_only,
            )
            deleted_id_set = set(deleted_element_ids)
            deleted_rows = [
                row for row in candidates if row["element_id"] in deleted_id_set
            ]
            deleted_in_batch = len(deleted_rows)
            total_deleted += deleted_in_batch
            self._released_count += deleted_in_batch
            if phase == "isolated":
                self._isolated_released_count += deleted_in_batch
            else:
                self._fallback_released_count += deleted_in_batch
            budget -= deleted_in_batch

            for row in deleted_rows:
                node_type = row.get("node_type", "unknown")
                self._node_type_counts[node_type] += 1
                audit.append(self._build_audit(row, now))

            logger.info(
                "ForgetService core: phase=%s batch=%d deleted=%d/%d "
                "remaining_budget=%d types=%s",
                phase,
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

    async def _auxiliary_clean_batch(self, batch_size: int) -> int:
        """按创建时间从旧到新软删除一批辅助节点。

        Args:
            batch_size: 本批最多软删除的辅助节点数。

        Returns:
            本批实际软删除数；返回 0 表示辅助候选已耗尽或写入未生效。
        """
        if batch_size <= 0:
            return 0
        connector = self._connector
        if connector is None:
            raise RuntimeError("ForgetService connector is not initialized")

        candidates = await forget_get_auxiliary_candidates(
            connector,
            self.ctx.end_user_id,
            batch_size,
            DIALOGUE_AUDIT_CONTENT_MAX_LENGTH,
        )
        if not candidates:
            self._auxiliary_candidate_query_empty = True
            return 0

        element_ids = [row["element_id"] for row in candidates]
        now = utcnow()
        deleted_element_ids = await self._soft_delete_candidates(
            candidates,
            now,
            require_isolated=False,
        )
        deleted_id_set = set(deleted_element_ids)
        deleted_rows = [
            row for row in candidates if row["element_id"] in deleted_id_set
        ]
        deleted_in_batch = len(deleted_rows)
        self._auxiliary_released_count += deleted_in_batch

        audit: list[ForgetLog] = []
        for row in deleted_rows:
            node_type = row.get("node_type", "unknown")
            self._auxiliary_node_type_counts[node_type] += 1
            audit.append(self._build_audit(row, now))
        self._sync_audit(audit)
        logger.info(
            "ForgetService auxiliary: batch=%d deleted=%d total_deleted=%d "
            "types=%s",
            len(element_ids),
            deleted_in_batch,
            self._auxiliary_released_count,
            dict(self._auxiliary_node_type_counts),
        )
        return deleted_in_batch

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
