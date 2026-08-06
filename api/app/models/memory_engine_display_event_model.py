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

    两类触发链路：
    - 记忆写入（WritePipeline）：一轮最多三条事件
      （EXTRACTION / CROSS_MODAL / EMOTION）；
    - Celery 定时任务：一轮最多一条事件
      （FORGETTING 来自配额驱动的定时遗忘整理，
      REFLECTION 来自 Layer 2 高频巡检或每日全量去重）。

    engine_type 的取值范围只由代码约定，PG 没有枚举类型也没有 CHECK 约束，
    新增引擎类型不需要 migration。
    """

    __tablename__ = "memory_engine_display_records"

    id = Column(UUID(as_uuid=True), default=uuid.uuid4, primary_key=True)
    end_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("end_users.id"),
        nullable=False,
    )
    operation_id = Column(UUID(as_uuid=True), nullable=False)
    # EXTRACTION / CROSS_MODAL / EMOTION / FORGETTING / REFLECTION
    engine_type = Column(String(32), nullable=False)
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
