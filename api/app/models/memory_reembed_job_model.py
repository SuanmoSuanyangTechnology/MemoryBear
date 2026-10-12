"""工作空间 embedding 模型变更后的存量向量重算任务状态机。

工作空间切换 embedding 底层模型后，存量记忆的向量由旧模型生成，与新模型
写入的向量不再可比。该表记录每次重算任务的进度与终态，供任务恢复、状态
查询与问题排查使用；细粒度进度（已完成的 end_user、分页游标）暂存 Redis。
"""

import uuid
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.utils.datetime_utils import utcnow_naive
from app.db import Base


class ReembedJobStatus(StrEnum):
    """重算任务状态机的状态集合。

    终态只有 ``succeeded`` 与 ``failed``。历史上还有一个 ``superseded``
    （"任务被更新的切换取代"），随"切换中禁止再切"的守卫（409 + 行锁）落地后
    已无生产者，遂连同它的一整套收尾机制一并移除。
    """

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ReembedUserStatus(StrEnum):
    """单个 end_user 在任务内的处理状态。

    ``failed`` 既可能是「还可重试」（``attempts`` 未超上限）也可能是终态，
    由 ``attempts`` 与调用方的上限共同判定，因此不设单独的终态集合。
    """

    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class MemoryReembedJob(Base):
    __tablename__ = "memory_reembed_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    # 槽位存的是 model_configs.id；模型名是该配置解析出的底层运行名。
    old_embedding_config_id = Column(String, nullable=True)
    new_embedding_config_id = Column(String, nullable=False)
    old_model_name = Column(String, nullable=True)
    new_model_name = Column(String, nullable=True)
    status = Column(
        String,
        nullable=False,
        default=ReembedJobStatus.pending.value,
        server_default=ReembedJobStatus.pending.value,
        index=True,
    )
    total_end_users = Column(Integer, nullable=False, default=0, server_default="0")
    processed_end_users = Column(Integer, nullable=False, default=0, server_default="0")
    total_nodes = Column(BigInteger, nullable=False, default=0, server_default="0")
    processed_nodes = Column(BigInteger, nullable=False, default=0, server_default="0")
    failed_nodes = Column(BigInteger, nullable=False, default=0, server_default="0")
    error = Column(Text, nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            + ", ".join(repr(status.value) for status in ReembedJobStatus)
            + ")",
            name="ck_memory_reembed_jobs_status",
        ),
        CheckConstraint(
            "processed_end_users <= total_end_users",
            name="ck_memory_reembed_jobs_end_user_progress",
        ),
        Index(
            "ix_memory_reembed_jobs_workspace_created",
            "workspace_id",
            "created_at",
        ),
        Index(
            "ix_memory_reembed_jobs_active",
            "workspace_id",
            postgresql_where=("status IN ('pending', 'running')"),
        ),
    )


class MemoryReembedJobUser(Base):
    """任务内单个 end_user 的持久化处理状态。

    这是「任务是否跑完」与「失败要不要重试」的**权威来源**，故意不放在 Redis：
    Redis 故障或键过期时，若完成判据依赖它，任务会永久停在 running；页级失败
    若只记在内存计数里，也无法表达"这个用户要重试"。

    Redis 只保留分页游标与实时计数（丢了最多重算一页，不影响正确性）。
    """

    __tablename__ = "memory_reembed_job_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("memory_reembed_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    end_user_id = Column(String, nullable=False)
    status = Column(
        String,
        nullable=False,
        default=ReembedUserStatus.pending.value,
        server_default=ReembedUserStatus.pending.value,
    )
    #: 已尝试次数：页级失败不推进游标，由它决定还能否重试。
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    #: 扇出时写下的该用户分母，取自 inventory 的按 label 实时聚合（只含真正需要
    #: 重算的向量节点）。不用 end_users.memory_count——那个含无向量字段的 label，
    #: 会把分母抬高，让已完成的任务显示 processed < total。
    total_nodes = Column(Integer, nullable=False, default=0, server_default="0")
    processed_nodes = Column(Integer, nullable=False, default=0, server_default="0")
    failed_nodes = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    updated_at = Column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "end_user_id",
            name="uq_memory_reembed_job_users_scope",
        ),
        CheckConstraint(
            "status IN ("
            + ", ".join(repr(status.value) for status in ReembedUserStatus)
            + ")",
            name="ck_memory_reembed_job_users_status",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_memory_reembed_job_users_attempts",
        ),
        Index(
            "ix_memory_reembed_job_users_pending",
            "job_id",
            "status",
        ),
    )
