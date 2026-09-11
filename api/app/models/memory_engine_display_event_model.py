"""记忆引擎展示事件 ORM 模型

每次引擎有效触发时写入一条不可变的事件记录。
PG 只是非关键的展示投影，采用尽力写入（best effort）。
查询时按指定时区下的自然日和引擎类型聚合事件，生成卡片。
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, UniqueConstraint, text
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
    # 冗余列：终端用户所属工作空间，支撑空间级聚合查询（免 JOIN end_users）。
    # nullable=True 兼容存量回填期与展示写入的容错语义（写入失败不影响主流程）。
    workspace_id = Column(
        UUID(as_uuid=True),
        nullable=True,
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
        # 空间级聚合分页支撑：按 workspace_id 过滤 + occurred_at 时间窗/排序。
        # 用户级查询走唯一约束前导列 end_user_id，此处仅为 end_user_id 缺省的
        # 空间级路径服务。
        Index(
            "idx_engine_display_ws_occurred",
            "workspace_id",
            occurred_at.desc(),
        ),
    )
