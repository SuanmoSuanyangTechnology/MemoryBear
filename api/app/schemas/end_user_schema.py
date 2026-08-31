import datetime
import uuid
from typing import Optional, List

from pydantic import BaseModel, Field
from pydantic import ConfigDict

from app.core.utils.datetime_utils import utcnow_naive
from app.schemas.response_schema import PageMeta


class EndUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="终端用户ID")
    app_id: Optional[uuid.UUID] = Field(description="应用ID", default=None)
    # end_user_id: str = Field(description="终端用户ID")
    other_id: Optional[str] = Field(description="第三方ID", default=None)
    other_name: Optional[str] = Field(description="其他名称", default="")
    other_address: Optional[str] = Field(description="其他地址", default="")
    reflection_time: Optional[datetime.datetime] = Field(description="反思时间", default_factory=utcnow_naive)
    write_time: Optional[datetime.datetime] = Field(description="最后写入时间", default=None)
    created_at: datetime.datetime = Field(description="创建时间", default_factory=utcnow_naive)
    updated_at: datetime.datetime = Field(description="更新时间", default_factory=utcnow_naive)
    
    # 用户摘要和洞察更新时间
    user_summary_updated_at: Optional[datetime.datetime] = Field(description="用户摘要最后更新时间", default=None)
    memory_insight_updated_at: Optional[datetime.datetime] = Field(description="洞察报告最后更新时间", default=None)
    #用户记忆节点总数（Neo4j模式）
    memory_count: int = Field(description="记忆节点总数", default=0)



class EndUserIdentityUpdate(BaseModel):
    """修改终端用户身份标识请求模型。

    end_user_id 用 str（与 EndUserInfoUpdate 对齐）：格式错误在函数体内返回
    HTTP 200 + code 9601，而非被 Pydantic 拦成 400。
    """
    end_user_id: str = Field(description="要修改的终端用户 ID（UUID 字符串）")
    identity_features: Optional[str] = Field(
        None,
        description="身份标识。传值=确认为长时身份(confirmed)并尝试归并；"
                    "传 null/空串/纯空白=清空标识并降级为临时身份(temporary)。",
    )


class EndUserMappingItem(BaseModel):
    """终端用户 ID 映射项"""
    end_user_id: str = Field(description="终端用户ID（UUID）")
    other_id: str = Field(description="外部用户标识")
    other_name: str = Field(description="用户名称")


class EndUserMappingResponse(BaseModel):
    """终端用户 ID 映射响应（分页）"""
    items: List[EndUserMappingItem] = Field(description="用户映射列表", default_factory=list)
    page: PageMeta = Field(description="分页信息")
