import uuid
import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from pydantic import ConfigDict


class UserAliasBase(BaseModel):
    """用户别名基础模型"""
    other_name: str = Field(description="关联的用户名称")
    alias: Optional[str] = Field(description="用户别名", default=None)
    meta_data: Optional[Dict[str, Any]] = Field(description="用户相关的扩展信息", default=None)


class UserAliasCreate(UserAliasBase):
    """创建用户别名请求模型"""
    end_user_id: uuid.UUID = Field(description="关联的终端用户ID")


class UserAliasUpdate(BaseModel):
    """更新用户别名请求模型"""
    alias: Optional[str] = Field(description="用户别名", default=None)
    meta_data: Optional[Dict[str, Any]] = Field(description="用户相关的扩展信息", default=None)


class UserAliasResponse(UserAliasBase):
    """用户别名响应模型"""
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID = Field(description="别名ID")
    end_user_id: uuid.UUID = Field(description="关联的终端用户ID")
    created_at: datetime.datetime = Field(description="创建时间")
    updated_at: datetime.datetime = Field(description="更新时间")
