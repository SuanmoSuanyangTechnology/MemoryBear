"""记忆展示记录 Schema

前端列表 DTO 和分页请求参数。
"""

from typing import Literal

from pydantic import BaseModel, Field


class MemoryDisplayRecordItem(BaseModel):
    """写入展示记录 - 前端列表项"""

    id: str = Field(..., description="PG 记录主键，作为前端列表项唯一 key")
    memory_id: str = Field(..., description="Neo4j MemorySummary.id，用于追溯源记忆")
    memory_type: Literal[
        "conversation",
        "project_work",
        "learning",
        "decision",
        "important_event",
    ] = Field(..., description="稳定英文记忆分类枚举，由前端负责文案映射")
    name: str = Field(..., description="标题")
    content: str = Field(..., description="内容")
    occurred_at: int = Field(..., description="展示记录写入时间，13 位 Unix 毫秒时间戳")
