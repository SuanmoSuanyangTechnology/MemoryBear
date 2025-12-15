from dataclasses import field
from pydantic import BaseModel, EmailStr, Field, field_validator, validator, ConfigDict
from typing import Optional
import datetime
import uuid

from app.models import Workspace
from app.models.workspace_model import WorkspaceRole


class UserBase(BaseModel):
    username: str
    email: EmailStr


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None


class ChangePasswordRequest(BaseModel):
    """修改密码请求"""
    old_password: str = Field(..., description="当前密码")
    new_password: str = Field(..., min_length=6, description="新密码，至少6位")


class AdminChangePasswordRequest(BaseModel):
    """管理员修改用户密码请求"""
    user_id: uuid.UUID = Field(..., description="要修改密码的用户ID")
    new_password: Optional[str] = Field(None, min_length=6, description="新密码，至少6位。如果不提供则自动生成随机密码")


class ChangePasswordResponse(BaseModel):
    """修改密码响应"""
    message: str
    success: bool = True
    generated_password: Optional[str] = Field(None, description="自动生成的密码（仅在管理员重置时返回）")


class User(UserBase):
    id: uuid.UUID
    is_active: bool
    is_superuser: bool
    created_at: int
    last_login_at: Optional[int] = None
    current_workspace_id: Optional[uuid.UUID] = None
    current_workspace_name: Optional[str] = None
    role: Optional[WorkspaceRole] = None

    # 将 datetime 转换为毫秒时间戳
    @validator("created_at", pre=True)
    def _created_at_to_ms(cls, v):
        if isinstance(v, datetime.datetime):
            return int(v.timestamp() * 1000)
        if isinstance(v, (int, float)):
            return int(v)
        return v

    model_config = ConfigDict(from_attributes=True)

    @field_validator("last_login_at", mode="before")
    @classmethod
    def _last_login_to_ms(cls, v):
        if v is None:
            return None
        if isinstance(v, datetime.datetime):
            return int(v.timestamp() * 1000)
        if isinstance(v, (int, float)):
            return int(v)
        return v

