"""反思日志 Repository"""
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from app.models.reflection_log_model import MemoryReflectionLog


class ReflectionLogRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(self, end_user_id: str, sub_problem: str, trigger_type: str,
               strategy: str, status: str, summary_text: str,
               entity_ids: Optional[List[str]] = None,
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
            trigger_detail=trigger_detail,
            solution_detail=solution_detail,
            execution_detail=execution_detail,
        )
        self.db.add(log)
        self.db.commit()
        return log