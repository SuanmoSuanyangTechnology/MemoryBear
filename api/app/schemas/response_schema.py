from pydantic import BaseModel, Field
from typing import Any, Optional
import time


class PageMeta(BaseModel):
    page: int = Field(..., description="当前页码，从1开始")
    pagesize: int = Field(..., description="每页数量")
    total: int = Field(..., description="总条数")
    hasnext: bool = Field(..., description="是否有下一页")

class PageData(BaseModel):
    page: PageMeta = Field(..., description="分页元数据")
    items: list = Field(..., description="分页数据列表")


class ApiResponse(BaseModel):
    code: int = Field(0, description="业务状态码，0=成功，非0=各类业务异常")
    msg: str = Field("OK", description="给人看的简短提示")
    data: Optional[Any] = Field(None, description="具体数据")
    error: str = Field("", description="失败时的字段级错误信息，成功时为空字符串")
    time: int = Field(default_factory=lambda: int(time.time()), description="Unix时间戳（秒）")