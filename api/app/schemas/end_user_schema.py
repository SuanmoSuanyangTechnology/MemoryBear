import uuid
import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from pydantic import ConfigDict

class EndUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="终端用户ID")
    app_id: Optional[uuid.UUID] = Field(description="应用ID", default=None)
    # end_user_id: str = Field(description="终端用户ID")
    other_id: Optional[str] = Field(description="第三方ID", default=None)
    other_name: Optional[str] = Field(description="其他名称", default="")
    other_address: Optional[str] = Field(description="其他地址", default="")
    reflection_time: Optional[datetime.datetime] = Field(description="反思时间", default_factory=datetime.datetime.now)
    created_at: datetime.datetime = Field(description="创建时间", default_factory=datetime.datetime.now)
    updated_at: datetime.datetime = Field(description="更新时间", default_factory=datetime.datetime.now)
    
    # 用户摘要和洞察更新时间
    user_summary_updated_at: Optional[datetime.datetime] = Field(description="用户摘要最后更新时间", default=None)
    memory_insight_updated_at: Optional[datetime.datetime] = Field(description="洞察报告最后更新时间", default=None)


class UserAliasResponse(BaseModel):
    """用户别名响应模型"""
    model_config = ConfigDict(from_attributes=True)
    
    user_alias_id: uuid.UUID = Field(description="用户别名记录ID")
    end_user_id: uuid.UUID = Field(description="终端用户ID")
    other_name: str = Field(description="用户名称")
    aliases: Optional[List[str]] = Field(description="别名列表", default=None)
    meta_data: Optional[dict] = Field(description="扩展信息", default=None)
    created_at: datetime.datetime = Field(description="创建时间")
    updated_at: datetime.datetime = Field(description="更新时间")


class UserAliasCreate(BaseModel):
    """创建用户别名请求模型"""
    end_user_id: str = Field(description="终端用户ID")
    other_name: str = Field(description="用户名称")
    aliases: Optional[List[str]] = Field(description="别名列表", default=None)
    meta_data: Optional[dict] = Field(description="扩展信息", default=None)


class UserAliasUpdate(BaseModel):
    """更新用户别名请求模型"""
    user_alias_id: str = Field(description="用户别名记录ID")
    other_name: Optional[str] = Field(description="用户名称", default=None)
    aliases: Optional[List[str]] = Field(description="别名列表", default=None)
    meta_data: Optional[dict] = Field(description="扩展信息", default=None)