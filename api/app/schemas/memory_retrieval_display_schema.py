"""记忆读取展示 Schema

包含：
- 支持落库的检索方式白名单
- 异步队列任务（不可变快照，不持有 ORM / Session / 请求上下文）
- 读取卡片前端 DTO
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 只有用户可见的四种检索方式会生成读取卡片；recent / meta 不在范围内。
RETRIEVE_SEARCH_MODES: frozenset[str] = frozenset({"deep", "normal", "quick", "express"})


@dataclass(frozen=True)
class RetrieveDisplayTask:
    """一次检索对应的读取展示快照。

    投递前即生成固定的 ``id`` / ``operation_id`` / ``occurred_at``，
    consumer 重试时复用同一份取值，配合 ``ON CONFLICT DO NOTHING`` 保证幂等。
    """

    id: uuid.UUID
    operation_id: uuid.UUID
    end_user_id: uuid.UUID
    search_mode: str
    query: str
    content: str
    occurred_at: datetime

    def to_row(self) -> dict:
        """转换为可直接用于批量 INSERT 的行。"""
        return {
            "id": self.id,
            "end_user_id": self.end_user_id,
            "operation_id": self.operation_id,
            "operation": "RETRIEVE",
            "memory_id": None,
            "memory_type": None,
            "name": None,
            "content": self.content,
            "score": None,
            "rank": None,
            "search_mode": self.search_mode,
            "query": self.query,
            "occurred_at": self.occurred_at,
        }


class MemoryRetrievalDisplayItem(BaseModel):
    """读取展示卡片 - 前端列表项"""

    id: str = Field(..., description="PG 记录主键，作为前端列表项唯一 key")
    search_mode: Literal["deep", "normal", "quick", "express"] = Field(
        ...,
        description="稳定英文检索方式枚举，由前端负责文案映射",
    )
    query: str = Field(..., description="预处理后、问题拆分前的主检索问题")
    content: str = Field(..., description="读取卡片正文，检索发生时已按当时语言聚合")
    occurred_at: int = Field(..., description="检索完成并投递的时间，13 位 Unix 毫秒时间戳")
