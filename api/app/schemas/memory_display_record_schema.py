"""记忆展示记录 Schema

前端列表 DTO 和分页请求参数。
"""

from pydantic import BaseModel, Field
from typing import Optional


class MemoryDisplayRecordItem(BaseModel):
    """写入展示记录 - 前端列表项"""

    id: str = Field(..., description="PG 记录主键，作为前端列表项唯一 key")
    memory_id: str = Field(..., description="Neo4j MemorySummary.id，用于追溯源记忆")
    memory_type: str = Field(..., description="记忆分类")
    name: str = Field(..., description="标题")
    content: str = Field(..., description="内容")
    created_at: int = Field(..., description="记忆形成时间，13 位 Unix 毫秒时间戳")


class WrittenMemoryQueryParams(BaseModel):
    """写入展示记录查询参数"""

    end_user_id: str = Field(..., description="终端用户 ID")
    page: int = Field(1, ge=1, description="页码，从 1 开始")
    pagesize: int = Field(10, ge=1, le=100, description="每页数量，默认 10，最大 100")
