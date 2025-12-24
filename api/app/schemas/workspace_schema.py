import datetime
import email
import uuid
from typing import Literal, Optional

from app.models.workspace_model import InviteStatus, WorkspaceRole
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_serializer,
)


class WorkspaceBase(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = None
    iconType: str | None = None
    storage_type: str | None = None
    llm: str | None = None
    embedding: str | None = None
    rerank: str | None = None


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


class Workspace(WorkspaceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class WorkspaceResponse(WorkspaceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime.datetime
    is_active: bool

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp()) if dt else None


class WorkspaceMemberBase(BaseModel):
    user_id: uuid.UUID
    role: WorkspaceRole


class WorkspaceMemberCreate(WorkspaceMemberBase):
    pass

class WorkspaceMemberUpdate(BaseModel):
    id: uuid.UUID
    role: WorkspaceRole

class WorkspaceMember(WorkspaceMemberBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    email: str


# 简版嵌套模型用于成员详情的关系序列化
class UserShort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: EmailStr


class WorkspaceShort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class WorkspaceMemberDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: WorkspaceRole
    is_active: bool
    user: UserShort
    workspace: WorkspaceShort


# 成员管理表格视图项（扁平化字段，便于前端表格渲染）
class WorkspaceMemberItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    account: EmailStr
    role: WorkspaceRole  # 原始角色值：manager | member
    last_login_at: datetime.datetime | None = None

    # 将最后登录时间序列化为毫秒时间戳，便于前端统一格式化
    @field_serializer("last_login_at", when_used="json")
    def _serialize_last_login(self, dt: datetime.datetime | None):
        return int(dt.timestamp() * 1000) if dt else None

    # # 动态计算角色中文标签
    # @computed_field
    # def role_label(self) -> str:
    #     return "管理员" if self.role == WorkspaceRole.manager else "成员"


# Workspace Invite Schemas
class WorkspaceInviteCreate(BaseModel):
    email: EmailStr = Field(..., description="被邀请者邮箱")
    role: WorkspaceRole = Field(..., description="邀请角色：manager 或 member")
    expires_in_days: int = Field(default=7, ge=1, le=30, description="邀请有效期天数，默认7天")


class WorkspaceInviteResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    email: str
    role: WorkspaceRole
    status: InviteStatus
    expires_at: datetime.datetime
    accepted_at: datetime.datetime | None
    created_by_user_id: uuid.UUID
    created_at: datetime.datetime
    invite_token: str | None = Field(None, description="邀请令牌，仅在创建时返回")

    @field_serializer("expires_at", when_used="json")
    def _serialize_expires_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("accepted_at", when_used="json")
    def _serialize_accepted_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    

class InviteValidateResponse(BaseModel):
    workspace_name: str
    workspace_id: uuid.UUID
    email: str
    role: WorkspaceRole
    is_expired: bool
    is_valid: bool


class InviteAcceptRequest(BaseModel):
    token: str = Field(..., description="邀请令牌")


class WorkspaceModelsUpdate(BaseModel):
    """工作空间模型配置更新请求"""
    llm: Optional[uuid.UUID] = Field(default=None, description="LLM模型ID")
    embedding: Optional[uuid.UUID] = Field(default=None, description="嵌入模型ID") 
    rerank: Optional[uuid.UUID] = Field(default=None, description="重排序模型ID")


class WorkspaceModelsConfig(BaseModel):
    """工作空间模型配置响应"""
    model_config = ConfigDict(from_attributes=True)
    
    llm: Optional[str] = Field(default=None, description="LLM模型ID")
    embedding: Optional[str] = Field(default=None, description="嵌入模型ID")
    rerank: Optional[str] = Field(default=None, description="重排序模型ID")
