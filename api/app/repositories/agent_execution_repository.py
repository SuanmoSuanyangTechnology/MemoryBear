"""Agent 执行记录 Repository"""
import uuid
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import utcnow_naive
from app.core.workflow.node_cache import sanitize_json_value
from app.models.agent_execution_model import AgentExecution


def _sanitize_jsonb_fields(updates: dict) -> dict:
    """剥离写入 JSONB 列的 NUL（U+0000）。

    Agent 的 knowledge_retrieval_tool 会把 ES 中 PDF/Office 解析出的 chunk 原文放进
    steps[].output/input，其中可能混入 NUL。PostgreSQL jsonb/text 无法表示 U+0000，
    写入即抛 asyncpg UntranslatableCharacterError，表现为会话末尾冒出 model_error。
    """
    for key in ("steps", "token_usage", "agent_log"):
        if updates.get(key) is not None:
            updates[key] = sanitize_json_value(updates[key])
    return updates


class AgentExecutionRepository:
    """Agent 执行记录数据访问层"""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def create(self, execution: AgentExecution) -> AgentExecution:
        """创建执行记录"""
        self.db.add(execution)
        self.db.flush()
        return execution

    async def create_async(self, execution: AgentExecution) -> AgentExecution:
        """异步创建执行记录"""
        self.db.add(execution)
        await self.db.flush()
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
        agent_log: Optional[dict] = None,
    ) -> None:
        """更新执行记录为完成状态"""
        updates = {
            "steps": steps,
            "status": status,
            "completed_at": completed_at or utcnow_naive(),
        }
        if elapsed_time is not None:
            updates["elapsed_time"] = elapsed_time
        if token_usage is not None:
            updates["token_usage"] = token_usage
        if error_message is not None:
            updates["error_message"] = error_message
        if message_id is not None:
            updates["message_id"] = message_id
        if agent_log is not None:
            updates["agent_log"] = agent_log

        updates = _sanitize_jsonb_fields(updates)

        stmt = (
            select(AgentExecution)
            .where(AgentExecution.id == execution_id)
        )
        record = self.db.scalars(stmt).first()
        if record:
            for k, v in updates.items():
                setattr(record, k, v)
            self.db.commit()

    async def update_completed_async(
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
        agent_log: Optional[dict] = None,
    ) -> None:
        """异步更新执行记录为完成状态"""
        updates = {
            "steps": steps,
            "status": status,
            "completed_at": completed_at or utcnow_naive(),
        }
        if elapsed_time is not None:
            updates["elapsed_time"] = elapsed_time
        if token_usage is not None:
            updates["token_usage"] = token_usage
        if error_message is not None:
            updates["error_message"] = error_message
        if message_id is not None:
            updates["message_id"] = message_id
        if agent_log is not None:
            updates["agent_log"] = agent_log

        updates = _sanitize_jsonb_fields(updates)

        result = await self.db.execute(
            select(AgentExecution).where(AgentExecution.id == execution_id)
        )
        record = result.scalar_one_or_none()
        if record:
            for k, v in updates.items():
                setattr(record, k, v)
            await self.db.commit()

    def get_by_conversation(
        self,
        conversation_id: uuid.UUID,
        agent_role: Optional[str] = None,
    ) -> list[AgentExecution]:
        """按会话 ID 查询执行记录（按时间正序）

        Args:
            conversation_id: 会话 ID
            agent_role: 可选，"master" / "sub"；多 Agent 日志列表必须传 "master"，
                否则子 Agent 记录会被当成独立执行吸附到对话消息上。
        """
        stmt = (
            select(AgentExecution)
            .where(AgentExecution.conversation_id == conversation_id)
        )
        if agent_role is not None:
            stmt = stmt.where(AgentExecution.agent_role == agent_role)
        stmt = stmt.order_by(AgentExecution.started_at.asc())
        return list(self.db.scalars(stmt).all())

    def list_by_parent(self, parent_execution_id: uuid.UUID) -> list[AgentExecution]:
        """按父执行 ID 查子 Agent 执行记录（按时间正序，多次激活即多行）"""
        stmt = (
            select(AgentExecution)
            .where(AgentExecution.parent_execution_id == parent_execution_id)
            .order_by(AgentExecution.started_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def list_sub_by_conversation(self, conversation_id: uuid.UUID) -> list[AgentExecution]:
        """按会话 ID 查所有子 Agent 执行记录（一次取回，避免逐 master 查 N+1）"""
        stmt = (
            select(AgentExecution)
            .where(
                AgentExecution.conversation_id == conversation_id,
                AgentExecution.agent_role == "sub",
            )
            .order_by(AgentExecution.started_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def count_sub_by_conversations(self, conversation_ids: list[uuid.UUID]) -> dict[str, int]:
        """批量统计每个会话的子 Agent 执行条数（列表页"子 Agent 数"列）。

        单条 GROUP BY 查询；任何异常由调用方降级为 0，不阻断日志列表。
        """
        if not conversation_ids:
            return {}
        stmt = (
            select(
                AgentExecution.conversation_id,
                func.count(AgentExecution.id),
            )
            .where(
                AgentExecution.conversation_id.in_(conversation_ids),
                AgentExecution.agent_role == "sub",
            )
            .group_by(AgentExecution.conversation_id)
        )
        rows = self.db.execute(stmt).all()
        return {str(row[0]): int(row[1]) for row in rows}

    def get_by_message_id(
        self,
        message_id: uuid.UUID,
        agent_role: Optional[str] = None,
    ) -> Optional[AgentExecution]:
        """按 message_id 查询执行记录

        Args:
            message_id: assistant 消息 ID
            agent_role: 可选；只看主 Agent 记录时传 "master"
        """
        stmt = (
            select(AgentExecution)
            .where(AgentExecution.message_id == message_id)
        )
        if agent_role is not None:
            stmt = stmt.where(AgentExecution.agent_role == agent_role)
        return self.db.scalars(stmt).first()
