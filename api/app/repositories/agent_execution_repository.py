"""Agent 执行记录 Repository"""
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_execution_model import AgentExecution


class AgentExecutionRepository:
    """Agent 执行记录数据访问层"""

    def __init__(self, db: Session):
        self.db = db

    def create(self, execution: AgentExecution) -> AgentExecution:
        """创建执行记录"""
        self.db.add(execution)
        self.db.flush()
        return execution

    def update_completed(
        self,
        execution_id: uuid.UUID,
        *,
        steps: list,
        status: str = "completed",
        elapsed_time: Optional[float] = None,
        token_usage: Optional[dict] = None,
        error_message: Optional[str] = None,
        completed_at=None,
        message_id: Optional[uuid.UUID] = None,
    ) -> None:
        """更新执行记录为完成状态"""
        import datetime as dt

        updates = {
            "steps": steps,
            "status": status,
            "completed_at": completed_at or dt.datetime.now(),
        }
        if elapsed_time is not None:
            updates["elapsed_time"] = elapsed_time
        if token_usage is not None:
            updates["token_usage"] = token_usage
        if error_message is not None:
            updates["error_message"] = error_message
        if message_id is not None:
            updates["message_id"] = message_id

        stmt = (
            select(AgentExecution)
            .where(AgentExecution.id == execution_id)
        )
        record = self.db.scalars(stmt).first()
        if record:
            for k, v in updates.items():
                setattr(record, k, v)
            self.db.commit()

    def get_by_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> list[AgentExecution]:
        """按会话 ID 查询所有执行记录（按时间正序）"""
        stmt = (
            select(AgentExecution)
            .where(AgentExecution.conversation_id == conversation_id)
            .order_by(AgentExecution.started_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_message_id(
        self,
        message_id: uuid.UUID,
    ) -> Optional[AgentExecution]:
        """按 message_id 查询执行记录"""
        stmt = (
            select(AgentExecution)
            .where(AgentExecution.message_id == message_id)
        )
        return self.db.scalars(stmt).first()
