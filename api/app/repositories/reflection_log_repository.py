"""反思日志 Repository"""
import uuid
from typing import Any, Dict, List, Optional,Tuple
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models.reflection_log_model import MemoryReflectionLog


class ReflectionLogRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(self, end_user_id: str, sub_problem: str, trigger_type: str,
               strategy: str, status: str, summary_text: str,
               entity_ids: Optional[List[str]] = None,
               statement_ids: Optional[List[str]] = None,
               trigger_detail: Optional[Dict] = None,
               solution_detail: Optional[Dict] = None,
               execution_detail: Optional[Dict] = None,
               baseline: Optional[str] = None,
               confidence: Optional[float] = None,
               ) -> MemoryReflectionLog:
        log = MemoryReflectionLog(
            id=uuid.uuid4(),
            end_user_id=uuid.UUID(end_user_id) if isinstance(end_user_id, str) else end_user_id,
            sub_problem=sub_problem,
            trigger_type=trigger_type,
            baseline=baseline,
            strategy=strategy,
            confidence=confidence,
            status=status,
            summary_text=summary_text,
            entity_ids=entity_ids,
            statement_ids=statement_ids,
            trigger_detail=trigger_detail,
            solution_detail=solution_detail,
            execution_detail=execution_detail,
        )
        self.db.add(log)
        self.db.commit()
        return log

    async def get_by_id_async(self, log_id: str) -> Optional[MemoryReflectionLog]:
        """Async: 按 ID 查询单条日志"""
        result = await self.db.execute(
            select(MemoryReflectionLog).where(MemoryReflectionLog.id == uuid.UUID(log_id))
        )
        return result.scalars().first()

    async def get_paginated_async(
        self,
        end_user_id: str,
        page: int = 1,
        pagesize: int = 10,
        sub_problem: Optional[str] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
    ) -> Tuple[int, list]:
        """Async: 分页查询反思日志"""
        stmt = select(MemoryReflectionLog).where(
            MemoryReflectionLog.end_user_id == uuid.UUID(end_user_id)
        )
        if sub_problem:
            stmt = stmt.where(MemoryReflectionLog.sub_problem == sub_problem)
        if status:
            stmt = stmt.where(MemoryReflectionLog.status == status)
        if trigger_type:
            stmt = stmt.where(MemoryReflectionLog.trigger_type == trigger_type)

        # count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar()

        # items
        stmt = stmt.order_by(MemoryReflectionLog.created_at.desc()).offset((page - 1) * pagesize).limit(pagesize)
        result = await self.db.execute(stmt)
        items = result.scalars().all()

        return total, items


    async def get_stats_async(self, end_user_id: str) -> Dict[str, Any]:
        """统计查询（异步版本）：按子问题和状态分组计数

        Args:
            end_user_id: 终端用户 ID

        Returns:
            同 get_stats
        """
        from sqlalchemy import select
        from app.schemas.memory_reflection_schemas import SubProblemEnum

        end_user_uuid = uuid.UUID(end_user_id)

        # total count
        total = int(await self.db.scalar(
            select(func.count()).select_from(MemoryReflectionLog).where(
                MemoryReflectionLog.end_user_id == end_user_uuid
            )
        ) or 0)

        # 按 sub_problem 分组
        sub_rows = (await self.db.execute(
            select(MemoryReflectionLog.sub_problem, func.count()).where(
                MemoryReflectionLog.end_user_id == end_user_uuid
            ).group_by(MemoryReflectionLog.sub_problem)
        )).all()
        sub_counts = {row[0]: row[1] for row in sub_rows}

        # 按 status 分组
        status_rows = (await self.db.execute(
            select(MemoryReflectionLog.status, func.count()).where(
                MemoryReflectionLog.end_user_id == end_user_uuid
            ).group_by(MemoryReflectionLog.status)
        )).all()
        status_counts = {row[0]: row[1] for row in status_rows}

        # 补全所有枚举值
        all_sub_problems = [e.value for e in SubProblemEnum]
        sub_problem = {sp: sub_counts.get(sp, 0) for sp in all_sub_problems}
        status = {
            "resolved": status_counts.get("resolved", 0),
            "recorded": status_counts.get("recorded", 0),
        }

        resolve_rate = round(status["resolved"] / total, 2) if total > 0 else 0.0

        return {
            "total": total,
            "sub_problem": sub_problem,
            "status": status,
            "resolve_rate": resolve_rate,
        }

    async def get_total_async(self, end_user_id: str) -> int:
        """获取反思日志总数（异步版本）。

        Args:
            end_user_id: 终端用户 ID。

        Returns:
            int: 总记录数。
        """
        from sqlalchemy import select
        end_user_uuid = uuid.UUID(end_user_id)
        total = await self.db.scalar(
            select(func.count()).select_from(MemoryReflectionLog).where(
                MemoryReflectionLog.end_user_id == end_user_uuid
            )
        )
        return int(total or 0)