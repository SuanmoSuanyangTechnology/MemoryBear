"""Skill-domain schemas (frozen port of the monolith skill_schema).

Field names, defaults, descriptions, and serializers must stay byte-compatible
with the host so responses remain wire-identical during coexistence. Only
import paths were rewritten mechanically.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_serializer
import uuid
from datetime import datetime

from ..utils.datetime_utils import to_timestamp_ms


class SkillBase(BaseModel):
    """Base skill schema."""
    name: str = Field(..., description="技能名称")
    description: Optional[str] = Field(None, description="技能描述")
    tools: List[Dict[str, str]] = Field(default_factory=list, description="工具对象列表: [{\"tool_id\": \"xxx\", \"operation\": \"yyy\"}]")
    config: Dict[str, Any] = Field(default_factory=dict, description="技能配置")
    prompt: Optional[str] = Field(None, description="技能专属提示词")
    is_active: bool = Field(True, description="是否激活")
    is_public: bool = Field(False, description="是否公开到市场")


class SkillCreate(SkillBase):
    """Create skill schema."""
    pass


class SkillUpdate(BaseModel):
    """Update skill schema."""
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[List[Dict[str, str]]] = None
    config: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None


class Skill(BaseModel):
    """Skill response schema."""
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: Optional[str] = None
    tools: Union[List[Dict[str, Any]], List[Dict[str, str]]] = Field(default_factory=list, description="工具列表，可以是简单格式或包含工具详情")
    config: Dict[str, Any] = Field(default_factory=dict)
    prompt: Optional[str] = None
    is_active: bool = True
    is_public: bool = False
    created_at: datetime
    updated_at: datetime

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime_to_timestamp(self, value: datetime) -> int:
        """Millisecond timestamp."""
        return to_timestamp_ms(value)

    class Config:
        from_attributes = True


class SkillQuery(BaseModel):
    """Skill query parameters."""
    search: Optional[str] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    page: int = Field(1, ge=1)
    pagesize: int = Field(10, ge=1, le=100)
