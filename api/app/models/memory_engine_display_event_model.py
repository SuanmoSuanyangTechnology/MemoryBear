"""记忆引擎展示事件 ORM 模型

每次引擎有效触发时写入一条不可变的事件记录。
PG 只是非关键的展示投影，采用尽力写入（best effort）。
查询时按指定时区下的自然日和引擎类型聚合事件，生成卡片。
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.utils.datetime_utils import utcnow_naive
from app.db import Base


class MemoryEngineDisplayEvent(Base):
    """引擎展示事件表

    记录每一次有效引擎触发的结构化事件。
    同一轮写入操作最多产生三条事件（EXTRACTION/CROSS_MODAL/EMOTION）。
    """

    __tablename__ = "memory_engine_display_records"

    id = Column(UUID(as_uuid=True), default=uuid.uuid4, primary_key=True)
    end_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("end_users.id"),
        nullable=False,
    )
    operation_id = Column(UUID(as_uuid=True), nullable=False)
    engine_type = Column(String(32), nullable=False)  # EXTRACTION / CROSS_MODAL / EMOTION
    details = Column(JSONB, nullable=False, server_default="{}")
    occurred_at = Column(
        DateTime, nullable=False, default=utcnow_naive
    )  # naive UTC

    __table_args__ = (
        UniqueConstraint(
            "end_user_id", "engine_type", "operation_id",
            name="uq_engine_display_user_type_op",
        ),
    )
