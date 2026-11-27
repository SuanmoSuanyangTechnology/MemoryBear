"""HTTP contracts for the migrated workspace memory-policy / re-embed routes.

Field names, constraints, defaults and timestamp encodings mirror the API's
``app/schemas/workspace_schema.py`` and ``app/schemas/response_schema.py`` for the
workspace routes under ``/workspaces`` that this module serves. The API proxy
forwards the caller's body and query unchanged, so request models must validate
exactly like the host controller's.

``WorkspaceApiResponse`` is the *untyped* envelope (``data`` is arbitrary JSON)
because the host controller declares FastAPI's plain ``ApiResponse`` for every
one of these routes; a typed ``ApiResponse[...]`` would change the published
response schema. Payload models are still described here so the handler returns a
validated shape rather than an untyped dict.

``MemoryReembedEndUserListData`` is the API's ``MemoryReembedEndUserListResponse``
as it appears on the wire: ``updated_at`` is an epoch-millisecond integer (the
host model's ``field_serializer`` converts the datetime), and ``page`` uses the
shared ``PageMeta`` shape rather than flat ``page``/``pagesize``/``total`` keys.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.common.pagination import PageMeta
from src.common.timing import now_ms


class WorkspaceApiResponse(BaseModel):
    """The legacy untyped response envelope (``data`` is arbitrary JSON)."""

    code: int = 0
    msg: str = "OK"
    data: Any | None = None
    error: str = ""
    time: int = Field(default_factory=now_ms)


class ReembedEndUserItem(BaseModel):
    """One end user's state inside a re-embed job."""

    end_user_id: str
    name: str | None = None
    external_id: str | None = None
    status: str = Field(
        ...,
        description=(
            "queued（从未被处理过，还没轮到它）/ "
            "running（处理中：正在跑，或失败后仍有重试余额、会被自动重派）/ "
            "succeeded（成功）/ failed（终态失败：重试余额耗尽，可人工重试）"
        ),
    )
    attempts: int = 0
    max_attempts: int = 0
    total_nodes: int = 0
    processed_nodes: int = 0
    failed_nodes: int = 0
    last_error: str | None = None
    #: Epoch milliseconds, matching the host model's serializer.
    updated_at: int | None = None


class MemoryReembedEndUserListData(BaseModel):
    """The API's per-user list payload for one re-embed job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str | None = Field(None, description="任务 id；无当前任务时为 null")
    job_status: str = Field("", description="任务状态；无当前任务时为空串")
    page: PageMeta = Field(..., description="分页信息（page/pagesize/total/hasnext）")
    summary: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "整个任务的四态计数（queued/running/succeeded/failed），"
            "不受 status 过滤影响"
        ),
    )
    items: list[ReembedEndUserItem] = Field(default_factory=list)


class MemoryReembedJobData(BaseModel):
    """The API's ``MemoryReembedJobResponse`` as it appears on the wire.

    Timestamps are epoch milliseconds (the host model's ``field_serializer``);
    ``end_users`` is the same four-bucket vocabulary as the end-user list's
    ``summary``, computed from the same predicates.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    status: str = Field(..., description="pending/running/succeeded/failed")
    old_model_name: str | None = Field(None, description="变更前的 embedding 底层模型名")
    new_model_name: str | None = Field(None, description="变更后的 embedding 底层模型名")
    total_end_users: int = 0
    processed_end_users: int = 0
    failed_nodes: int = Field(0, description="失败节点数")
    end_users: dict[str, int] = Field(
        default_factory=dict,
        description="按四态分组的 end_user 计数，恒四键",
    )
    error: str | None = None
    created_at: int | None = None
    started_at: int | None = None
    finished_at: int | None = None


class WorkspaceReembedJobCreate(BaseModel):
    """Body of ``POST /workspaces/workspace_reembed/jobs``.

    Service-to-service: the workspace service only writes the workspace's model
    columns, then calls this endpoint so the rest of the switch runs here — the
    underlying model names are resolved server-side from ``model_configs.name``
    (the host's ``model_name``), exactly like the API's own switch flow.

    ``old_embedding_config_id`` is the slot value the caller read *before* its
    write (``null`` when the workspace had no embedding). It decides whether the
    embedding slot actually changed; ``new_embedding_config_id`` must already be
    the workspace's current ``embedding`` value, otherwise registration is
    refused (the driver would self-abort on a job that targets a stale model).
    """

    model_config = ConfigDict(extra="forbid")

    old_embedding_config_id: UUID | None = Field(
        ...,
        description="变更前的 embedding 配置 id；工作空间原本无 embedding 时传 null",
    )
    new_embedding_config_id: UUID = Field(
        ...,
        description="新的 embedding 配置 id；必须已是工作空间当前模型",
    )


class WorkspaceReembedJobCreatedData(BaseModel):
    """``data`` of the job-creation endpoint.

    ``job_id`` is "the job in flight after this request", the same value the API
    back-fills into ``reembed_job_id``: a newly registered job, the job already
    running when the switch needed no rebuild, or ``null`` when nothing is in
    flight. ``created`` says whether this call inserted the row.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str | None = Field(
        None, description="本次请求之后在途的任务 id；无在途任务时为 null"
    )
    #: ``None`` when no task was published (no-op, or the broker rejected it).
    task_id: str | None = None
    created: bool = Field(..., description="本次调用是否新建了重算任务")


class WorkspaceRetentionPolicyUpdate(BaseModel):
    retention_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="临时身份保留天数，null 表示永不过期",
    )
