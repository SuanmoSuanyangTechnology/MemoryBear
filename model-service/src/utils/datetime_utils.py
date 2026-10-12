"""时间助手：DB 存裸 UTC，对外序列化 unix 毫秒。"""

from __future__ import annotations

from datetime import UTC, datetime

from ..models.base import utcnow_naive

__all__ = ["as_utc_aware", "to_timestamp_ms", "utcnow_naive"]


def as_utc_aware(value: datetime | None) -> datetime | None:
    """裸 datetime 按项目约定视作 UTC。"""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_timestamp_ms(value: datetime | None) -> int | None:
    """datetime → UTC unix 毫秒（响应时间字段统一口径）。"""

    aware = as_utc_aware(value)
    if aware is None:
        return None
    return int(aware.timestamp() * 1000)
