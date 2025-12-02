import uuid
import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_serializer
from pydantic import ConfigDict

class MemoryIncrement(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: uuid.UUID = Field(description="工作空间ID")
    total_num: int = Field(description="增量总数")    
    created_at: datetime.datetime = Field(description="创建时间", default_factory=datetime.datetime.now())
    updated_at: datetime.datetime = Field(description="更新时间", default_factory=datetime.datetime.now())

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime(self, dt: datetime.datetime, _info) -> str:
        """将日期时间序列化为年月日格式"""
        return dt.strftime('%Y-%m-%d')
