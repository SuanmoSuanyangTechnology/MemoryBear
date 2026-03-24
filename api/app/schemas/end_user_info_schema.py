import uuid
import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from pydantic import ConfigDict


class EndUserInfoBase(BaseModel):
    """终端用户信息基础模型"""
    other_name: str = Field(description="关联的用户名称")
    aliases: Optional[List[str]] = Field(description="用户别名列表", default=None)
    meta_data: Optional[Dict[str, Any]] = Field(description="用户相关的扩展信息", default=None)


class EndUserInfoCreate(EndUserInfoBase):
    """创建终端用户信息请求模型"""
    end_user_id: str = Field(description="关联的终端用户ID")


class EndUserInfoUpdate(BaseModel):
    """更新终端用户信息请求模型"""
    end_user_info_id: str = Field(description="终端用户信息记录ID")
    other_name: Optional[str] = Field(description="用户名称", default=None)
    aliases: Optional[List[str]] = Field(description="用户别名列表", default=None)
    meta_data: Optional[Dict[str, Any]] = Field(description="用户相关的扩展信息", default=None)


class EndUserInfoResponse(EndUserInfoBase):
    """终端用户信息响应模型"""
    model_config = ConfigDict(from_attributes=True)
    
    end_user_info_id: uuid.UUID = Field(description="终端用户信息记录ID")
    end_user_id: uuid.UUID = Field(description="关联的终端用户ID")
    created_at: datetime.datetime = Field(description="创建时间")
    updated_at: datetime.datetime = Field(description="更新时间")
