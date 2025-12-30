import uuid
import datetime
from typing import Optional
from pydantic import BaseModel, Field
from pydantic import ConfigDict

class EndUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="终端用户ID")
    app_id: uuid.UUID = Field(description="应用ID")
    # end_user_id: str = Field(description="终端用户ID")
    other_id: Optional[str] = Field(description="第三方ID", default=None)
    other_name: Optional[str] = Field(description="其他名称", default="")
    other_address: Optional[str] = Field(description="其他地址", default="")
    reflection_time: Optional[datetime.datetime] = Field(description="反思时间", default_factory=datetime.datetime.now)
    created_at: datetime.datetime = Field(description="创建时间", default_factory=datetime.datetime.now)
    updated_at: datetime.datetime = Field(description="更新时间", default_factory=datetime.datetime.now)
    
    # 用户基本信息字段
    position: Optional[str] = Field(description="职位", default=None)
    department: Optional[str] = Field(description="部门", default=None)
    contact: Optional[str] = Field(description="联系方式", default=None)
    phone: Optional[str] = Field(description="电话", default=None)
    hire_date: Optional[int] = Field(description="入职日期（时间戳，毫秒）", default=None)
    updatetime_profile: Optional[int] = Field(description="核心档案信息最后更新时间（时间戳，毫秒）", default=None)


class EndUserProfileResponse(BaseModel):
    """终端用户基本信息响应模型"""
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID = Field(description="终端用户ID")
    other_name: Optional[str] = Field(description="其他名称", default="")
    position: Optional[str] = Field(description="职位", default=None)
    department: Optional[str] = Field(description="部门", default=None)
    contact: Optional[str] = Field(description="联系方式", default=None)
    phone: Optional[str] = Field(description="电话", default=None)
    hire_date: Optional[int] = Field(description="入职日期（时间戳，毫秒）", default=None)
    updatetime_profile: Optional[int] = Field(description="核心档案信息最后更新时间（时间戳，毫秒）", default=None)


class EndUserProfileUpdate(BaseModel):
    """终端用户基本信息更新请求模型"""
    end_user_id: str = Field(description="终端用户ID")
    other_name: Optional[str] = Field(description="其他名称", default="")
    position: Optional[str] = Field(description="职位", default=None)
    department: Optional[str] = Field(description="部门", default=None)
    contact: Optional[str] = Field(description="联系方式", default=None)
    phone: Optional[str] = Field(description="电话", default=None)
    hire_date: Optional[int] = Field(description="入职日期（时间戳，毫秒）", default=None)