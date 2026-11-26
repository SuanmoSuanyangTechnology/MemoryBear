"""User payloads for the management plane (host wire-compatible).

Field names, constraints and millisecond timestamp rendering mirror
``core/api/app/schemas/user_schema.py`` for the schemas the migrated routes
consume. ``UserUpdate`` / ``ChangeEmailRequest`` / ``ChangePasswordResponse``
are not ported: no migrated route consumes them.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.core.datetime_utils import to_timestamp_ms
from src.models.workspace_model import WorkspaceRole


class UserBase(BaseModel):
    username: str
    email: EmailStr


class UserCreate(UserBase):
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="当前密码")
    new_password: str = Field(..., min_length=6, description="新密码，至少6位")


class AdminChangePasswordRequest(BaseModel):
    user_id: uuid.UUID = Field(..., description="要修改密码的用户ID")
    new_password: str | None = Field(
        None,
        min_length=6,
        description="新密码，至少6位。如果不提供则自动生成随机密码",
    )


class SendEmailCodeRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱地址")


class VerifyEmailCodeRequest(BaseModel):
    new_email: EmailStr = Field(..., description="新邮箱地址")
    code: str = Field(..., min_length=6, max_length=6, description="验证码")


class VerifyPasswordRequest(BaseModel):
    password: str = Field(..., description="密码")


class LanguagePreferenceRequest(BaseModel):
    language: str = Field(
        ...,
        min_length=2,
        max_length=10,
        description="语言代码，如 'zh', 'en'",
    )


class LanguagePreferenceResponse(BaseModel):
    language: str = Field(..., description="当前语言偏好")


class User(UserBase):
    id: uuid.UUID
    is_active: bool
    is_superuser: bool
    created_at: int
    last_login_at: int | None = None
    current_workspace_id: uuid.UUID | None = None
    current_workspace_name: str | None = None
    role: WorkspaceRole | None = None
    preferred_language: str | None = "zh"
    phone: str | None = None
    permissions: list[str] | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_at_to_ms(cls, value):
        if isinstance(value, datetime.datetime):
            return to_timestamp_ms(value)
        if isinstance(value, (int, float)):
            return int(value)
        return value

    @field_validator("last_login_at", mode="before")
    @classmethod
    def _last_login_to_ms(cls, value):
        if value is None:
            return None
        if isinstance(value, datetime.datetime):
            return to_timestamp_ms(value)
        if isinstance(value, (int, float)):
            return int(value)
        return value

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
