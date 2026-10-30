"""Workspace value objects (eligibility verdict and statistics)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


class WorkspaceEligibility(BaseModel):
    """The eligibility verdict for cache generation against one workspace."""

    valid: bool
    reason: str
    storage_type: str | None = None


def _change_ratio(today_count: int, yesterday_count: int) -> float | None:
    if yesterday_count == 0:
        return None
    return round((today_count - yesterday_count) / yesterday_count, 4)


class WorkspaceStatistic(BaseModel):
    """单个记忆类型的统计项；``change`` 是与昨日快照的环比。"""

    type: str
    count: int
    percentage: float
    change: float | None


class WorkspaceStatistics(BaseModel):
    """工作空间统计的最终响应载体（同时是 ``workspace_statistics`` 的响应模型）。"""

    statistics: list[WorkspaceStatistic]
    total_count: int
    total_users: int
    #: 当前快照写的是 epoch 毫秒；旧快照在校验里允许 ISO-8601 字符串。
    generated_at: int | str | None
    total_count_change: float | None
    update: int

    @classmethod
    def from_refreshed(cls, response: Mapping[str, Any]) -> WorkspaceStatistics:
        yesterday = response["yesterday"]
        yesterday_counts = {
            item["type"]: item["count"] for item in yesterday["statistics"]
        }
        statistics = [
            {
                **item,
                "change": _change_ratio(
                    item["count"], yesterday_counts.get(item["type"], 0)
                ),
            }
            for item in response["statistics"]
        ]
        statistics.sort(key=lambda item: item["count"], reverse=True)
        return cls(
            statistics=statistics,
            total_count=response["total_count"],
            total_users=response["total_users"],
            generated_at=response.get("generated_at"),
            total_count_change=_change_ratio(
                response["total_count"], yesterday["total_count"]
            ),
            update=response["total_count"] - yesterday["total_count"],
        )
