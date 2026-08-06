"""记忆展示记录 ORM 模型

PG 仅保存前端展示快照，不参与记忆检索，Neo4j 仍是记忆事实源。
写入和读取共用一张表 memory_display_records，通过 operation 区分。
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.utils.datetime_utils import utcnow_naive
from app.db import Base


class MemoryDisplayRecord(Base):
    """记忆展示记录表

    记录每一次成功写入或检索操作的展示快照，
    前端写入区域和读取区域分别只查询对应 operation 的记录。

    - ``WRITE``：一次写入可以对应多条 MemorySummary，保持按记忆一行；
    - ``RETRIEVE``：一次检索只对应一行聚合读取卡片，
      ``memory_id / memory_type / name`` 不使用，正文统一写入 ``content``。
    """

    __tablename__ = "memory_display_records"

    id = Column(UUID(as_uuid=True), default=uuid.uuid4, primary_key=True)
    end_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("end_users.id"),
        nullable=False,
    )
    operation_id = Column(UUID(as_uuid=True), nullable=False)
    operation = Column(String(16), nullable=False)  # "WRITE" or "RETRIEVE"
    # 以下三列仅 WRITE 使用，RETRIEVE 存 NULL
    memory_id = Column(String(64), nullable=True)
    memory_type = Column(String(32), nullable=True)
    name = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    score = Column(Float, nullable=True)
    rank = Column(Integer, nullable=True)
    search_mode = Column(String(16), nullable=True)
    # 仅 RETRIEVE 使用：预处理后、问题拆分前的主检索问题
    query = Column(Text, nullable=True)
    occurred_at = Column(
        DateTime, nullable=False, default=utcnow_naive
    )  # naive UTC

    __table_args__ = (
        UniqueConstraint(
            "end_user_id", "operation_id", "operation", "memory_id",
            name="uq_memory_display_records_user_op_memory",
        ),
        # PostgreSQL 唯一约束允许多个 NULL，RETRIEVE 的 memory_id 为 NULL，
        # 因此额外用部分唯一索引保证「一次检索一行」。
        Index(
            "uq_memory_display_retrieve_user_operation",
            "end_user_id",
            "operation_id",
            unique=True,
            postgresql_where=text("operation = 'RETRIEVE'"),
        ),
        Index(
            "idx_memory_display_retrieve_user_occurred",
            "end_user_id",
            occurred_at.desc(),
            id.desc(),
            postgresql_where=text("operation = 'RETRIEVE'"),
        ),
    )
