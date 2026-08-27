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


@dataclass(frozen=True)
class YuqueDocInfo:
    """Document metadata returned by the Yuque repository APIs."""

    id: int
    type: str
    slug: str
    title: str
    book_id: int
    format: str
    body: str | None
    body_draft: str | None
    body_html: str | None
    public: int
    status: int
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    word_count: int
    cover: str | None
    description: str | None


__all__ = ["YuqueDocInfo", "YuqueRepoInfo"]
