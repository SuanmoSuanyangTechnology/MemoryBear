"""反思日志 Repository"""
import uuid
from typing import Any, Dict, List, Optional,Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func
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

    def get_paginated(
        self,
        end_user_id: str,
        page: int = 1,
        pagesize: int = 10,
        sub_problem: Optional[str] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
    ) -> Tuple[int, list]:
        """分页查询反思日志

        Args:
            end_user_id: 终端用户 ID
            page: 页码（从1开始）
            pagesize: 每页数量
            sub_problem: 子问题类型筛选（可选）
            status: 状态筛选（可选）
            trigger_type: 触发方式筛选（可选）

        Returns:
            (total, items): 总数和当前页 ORM 对象列表
        """
        query = self.db.query(MemoryReflectionLog).filter(
            MemoryReflectionLog.end_user_id == uuid.UUID(end_user_id)
        )

        if sub_problem:
            query = query.filter(MemoryReflectionLog.sub_problem == sub_problem)
        if status:
            query = query.filter(MemoryReflectionLog.status == status)
        if trigger_type:
            query = query.filter(MemoryReflectionLog.trigger_type == trigger_type)

        total = query.count()
        items = query.order_by(
            MemoryReflectionLog.created_at.desc()
        ).offset((page - 1) * pagesize).limit(pagesize).all()

        return total, items


    def get_by_id(self, log_id: str) -> Optional[MemoryReflectionLog]:
        """按 ID 查询单条日志

        Args:
            log_id: 日志 UUID 字符串

        Returns:
            MemoryReflectionLog 或 None
        """
        return self.db.query(MemoryReflectionLog).filter(
            MemoryReflectionLog.id == uuid.UUID(log_id)
        ).first()


    def get_stats(self, end_user_id: str) -> Dict[str, Any]:
        """统计查询：按子问题和状态分组计数

        Args:
            end_user_id: 终端用户 ID

        Returns:
            {
                "total": int,
                "sub_problem": {"entity_dedup": 28, ...},
                "status": {"resolved": 42, "recorded": 5},
                "resolve_rate": 0.89
            }
        """
        base = self.db.query(MemoryReflectionLog).filter(
            MemoryReflectionLog.end_user_id == uuid.UUID(end_user_id)
        )

        total = base.count()

        # 按 sub_problem 分组计数
        sub_counts = dict(
            base.with_entities(
                MemoryReflectionLog.sub_problem,
                func.count()
            ).group_by(MemoryReflectionLog.sub_problem).all()
        )

        # 按 status 分组计数
        status_counts = dict(
            base.with_entities(
                MemoryReflectionLog.status,
                func.count()
            ).group_by(MemoryReflectionLog.status).all()
        )

        # 补全所有枚举值（确保前端拿到完整结构）
        from app.schemas.memory_reflection_schemas import SubProblemEnum
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