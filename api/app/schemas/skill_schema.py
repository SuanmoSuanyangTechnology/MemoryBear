"""Skill Schema 定义"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_serializer
import uuid
from datetime import datetime


class SkillBase(BaseModel):
    """Skill 基础 Schema"""
    name: str = Field(..., description="技能名称")
    description: Optional[str] = Field(None, description="技能描述")
    tools: List[Dict[str, str]] = Field(default_factory=list, description="工具对象列表: [{\"tool_id\": \"xxx\", \"operation\": \"yyy\"}]")
    config: Dict[str, Any] = Field(default_factory=dict, description="技能配置")
    prompt: Optional[str] = Field(None, description="技能专属提示词")
    is_active: bool = Field(True, description="是否激活")
    is_public: bool = Field(False, description="是否公开到市场")


class SkillCreate(SkillBase):
    """创建 Skill"""
    pass


class SkillUpdate(BaseModel):
    """更新 Skill"""
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[List[Dict[str, str]]] = None
    config: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None


class Skill(SkillBase):
    """Skill 响应 Schema"""
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime_to_timestamp(self, value: datetime) -> int:
        """（毫秒级）时间戳"""
        return int(value.timestamp() * 1000)

    class Config:
        from_attributes = True


class SkillQuery(BaseModel):
    """Skill 查询参数"""
    search: Optional[str] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    page: int = Field(1, ge=1)
    pagesize: int = Field(10, ge=1, le=100)
