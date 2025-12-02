import uuid
import datetime
from typing import Optional, Text
from pydantic import BaseModel, Field
from pydantic import ConfigDict

class Host(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="宿主ID")
    host_id: uuid.UUID = Field(description="其他ID")
    retrieve_info: Optional[Text] = Field(description="检索信息")
    created_at: datetime.datetime = Field(description="创建时间", default_factory=datetime.datetime.now)
