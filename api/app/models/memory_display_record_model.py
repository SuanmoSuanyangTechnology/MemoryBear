"""记忆展示记录 ORM 模型

PG 仅保存前端展示快照，不参与记忆检索，Neo4j 仍是记忆事实源。
写入和读取共用一张表 memory_display_records，通过 operation 区分。
"""

import uuid

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db import Base


class MemoryDisplayRecord(Base):
    """记忆展示记录表

    记录每一次成功写入或检索操作的展示快照，
    前端写入区域和读取区域分别只查询对应 operation 的记录。
    """

    __tablename__ = "memory_display_records"

    id = Column(UUID(as_uuid=True), default=uuid.uuid4, primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    operation_id = Column(UUID(as_uuid=True), nullable=False)
    operation = Column(String(16), nullable=False)  # "WRITE" or "RETRIEVE"
    memory_id = Column(String(64), nullable=False)
    memory_type = Column(String(32), nullable=False)
    name = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    score = Column(Float, nullable=True)
    rank = Column(Integer, nullable=True)
    search_mode = Column(String(16), nullable=True)
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "end_user_id", "operation_id", "operation", "memory_id",
            name="uq_memory_display_records_user_op_memory",
        ),
    )
