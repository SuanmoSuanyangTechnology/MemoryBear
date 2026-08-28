"""Yuque response snapshots used by authentication checks."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class YuqueRepoInfo:
    id: int
    type: str
    name: str
    namespace: str
    slug: str
    description: str | None
    public: int
    items_count: int
    created_at: datetime | None
    updated_at: datetime | None


__all__ = ["YuqueRepoInfo"]
