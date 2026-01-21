"""
显性记忆的请求和响应模型
"""
from typing import Optional

from pydantic import BaseModel, Field

class ExplicitMemoryOverviewRequest(BaseModel):
    """显性记忆总览查询请求"""
    
    end_user_id: str = Field(..., description="终端用户ID")
    language_type: Optional[str] = Field("zh", description="语言类型（zh/en）")

class ExplicitMemoryDetailsRequest(BaseModel):
    """显性记忆详情查询请求"""
    
    end_user_id: str = Field(..., description="终端用户ID")
    memory_id: str = Field(..., description="记忆ID（情景记忆或语义记忆的ID）")
    language_type: Optional[str] = Field("zh", description="语言类型（zh/en）")
