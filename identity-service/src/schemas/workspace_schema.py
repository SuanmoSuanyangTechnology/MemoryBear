import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.core.datetime_utils import to_timestamp_ms
from src.models.workspace_model import InviteStatus, WorkspaceRole


def _to_ms(value: Any) -> int | None:
    if value is None or isinstance(value, int):
        return value
    return to_timestamp_ms(value)


class WorkspaceBase(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = None
    iconType: str | None = None
    storage_type: str | None = None
    is_default_config: bool = False
    llm: str | None = None
    embedding: str | None = None
    rerank: str | None = None
    vision: str | None = None
    audio: str | None = None
    video: str | None = None


class WorkspaceCreate(WorkspaceBase):
    pass


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(None)
    description: str | None = Field(None)
    icon: str | None = Field(None)
    iconType: str | None = Field(None)
    storage_type: str | None = Field(None)
    llm: str | None = Field(None)
    embedding: str | None = Field(None)
    rerank: str | None = Field(None)


class WorkspaceRetentionPolicyResponse(BaseModel):
    retention_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="临时身份保留天数，null 表示永不过期",
    )
    end_user_count: int | None = Field(
        default=None,
        ge=0,
        description="当前工作空间中至少有一条记忆的有效临时 EndUser 数量",
    )


class WorkspaceRetentionPolicyUpdate(BaseModel):
    retention_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="临时身份保留天数，null 表示永不过期",
    )


class WorkspaceResponse(WorkspaceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: int
    is_active: bool

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_at_ms(cls, value):
        return _to_ms(value)


class WorkspaceMemberUpdate(BaseModel):
    id: uuid.UUID
    role: WorkspaceRole


class WorkspaceMemberItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    account: EmailStr
    role: WorkspaceRole
    last_login_at: int | None = None

    @field_validator("last_login_at", mode="before")
    @classmethod
    def _last_login_ms(cls, value):
        return _to_ms(value)


class WorkspaceInviteCreate(BaseModel):
    email: EmailStr = Field(..., description="被邀请者邮箱")
    role: WorkspaceRole = Field(..., description="邀请角色：manager 或 member")
    expires_in_days: int = Field(default=7, ge=1, le=30, description="邀请有效期天数，默认7天")


class WorkspaceInviteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    email: str
    role: WorkspaceRole
    status: InviteStatus
    expires_at: int
    accepted_at: int | None = None
    created_by_user_id: uuid.UUID
    created_at: int
    invite_token: str | None = Field(None, description="邀请令牌，仅在创建时返回")

    @field_validator("expires_at", "created_at", mode="before")
    @classmethod
    def _required_ms(cls, value):
        return value if isinstance(value, int) else to_timestamp_ms(value)

    @field_validator("accepted_at", mode="before")
    @classmethod
    def _accepted_at_ms(cls, value):
        return _to_ms(value)


class InviteValidateResponse(BaseModel):
    workspace_name: str
    workspace_id: uuid.UUID
    email: str
    role: WorkspaceRole
    is_expired: bool
    is_valid: bool


class InviteAcceptRequest(BaseModel):
    token: str = Field(..., description="邀请令牌")
