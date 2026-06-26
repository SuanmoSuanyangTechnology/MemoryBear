"""V1 API response schemas for OpenAPI documentation."""
from typing import Generic, TypeVar, Optional, List
from pydantic import BaseModel, Field
import time

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应信封。"""
    code: int = Field(0, description="业务状态码，0=成功")
    msg: str = Field("OK", description="简短提示")
    data: Optional[T] = Field(None, description="业务数据")
    error: str = Field("", description="错误详情")
    time: int = Field(default_factory=lambda: int(time.time() * 1000), description="毫秒时间戳")


class NodeStatItem(BaseModel):
    """单个记忆类型统计项。"""
    type: str = Field(..., description="记忆节点类型")
    count: int = Field(..., description="数量")
    percentage: float = Field(..., description="占比")


class NodeStatisticsResponse(ApiResponse[List[NodeStatItem]]):
    """GET /v1/memory/analytics/node_statistics 响应。"""
    pass
