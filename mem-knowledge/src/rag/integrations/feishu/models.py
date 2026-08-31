"""Feishu response snapshots used by authentication checks."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FileInfo:
    token: str
    name: str
    type: str
    created_time: datetime | None = None
    modified_time: datetime | None = None
    owner_id: str = ""
    url: str = ""


__all__ = ["FileInfo"]
