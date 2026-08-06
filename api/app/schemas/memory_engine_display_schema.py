"""记忆引擎展示 Schema

引擎卡片 DTO 和分页请求参数。
"""

from typing import Literal

from pydantic import BaseModel, Field


class EngineDisplayCardItem(BaseModel):
    """引擎展示卡片 - 前端列表项"""

    id: str = Field(..., description="确定性聚合卡片 ID（UUID v5）")
    engine_type: Literal[
        "EXTRACTION",
        "CROSS_MODAL",
        "EMOTION",
        "FORGETTING",
        "REFLECTION",
    ] = Field(
        ...,
        description="稳定英文引擎枚举，由前端负责文案映射",
    )
    name: str = Field(..., description="根据 X-Language-Type 本地化的第一人称名称")
    content: str = Field(..., description="根据 X-Language-Type 本地化的成果描述")
    occurred_at: int = Field(..., description="组内最后一次有效引擎事件时间，13 位毫秒时间戳")
