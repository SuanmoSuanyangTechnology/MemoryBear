from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List
import datetime
import uuid
from app.core.exceptions import ValidationException
from app.core.error_codes import BizCode


class TenantBase(BaseModel):
    """租户基础Schema"""
    name: str = Field(..., description="租户名称", max_length=255)
    description: Optional[str] = Field(None, description="租户描述", max_length=1000)
    is_active: bool = Field(True, description="是否激活")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValidationException('租户名称不能为空', code=BizCode.VALIDATION_FAILED)
        return v.strip()


class TenantCreate(TenantBase):
    """创建租户Schema"""
    pass


class TenantUpdate(BaseModel):
    """更新租户Schema"""
    name: Optional[str] = Field(None, description="租户名称", max_length=255)
    description: Optional[str] = Field(None, description="租户描述", max_length=1000)
    is_active: Optional[bool] = Field(None, description="是否激活")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if v is not None and (not v or not v.strip()):
            raise ValidationException('租户名称不能为空', code=BizCode.VALIDATION_FAILED)
        return v.strip() if v else v


class Tenant(TenantBase):
    """租户Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime


class TenantQuery(BaseModel):
    """租户查询Schema"""
    is_active: Optional[bool] = Field(None, description="激活状态筛选")
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)
    page: int = Field(1, description="页码", ge=1)
    size: int = Field(10, description="每页数量", ge=1, le=100)


class TenantList(BaseModel):
    """租户列表响应Schema"""
    items: List[Tenant]
    total: int
    page: int
    size: int
    pages: int