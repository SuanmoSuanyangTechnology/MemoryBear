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