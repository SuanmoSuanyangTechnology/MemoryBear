"""Response contracts shared by the migrated management endpoints."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field


class PageMeta(BaseModel):
    page: int = Field(..., description="Current page number, starting at 1")
    pagesize: int = Field(..., description="Items per page")
    total: int = Field(..., description="Total number of items")
    hasnext: bool = Field(..., description="Whether another page exists")


class PageData(BaseModel):
    page: PageMeta = Field(..., description="Pagination metadata")
    items: list = Field(..., description="Page items")


class ApiResponse(BaseModel):
    code: int = Field(0, description="Business status code, 0 = success")
    msg: str = Field("OK", description="Short human-readable message")
    data: Any | None = Field(None, description="Payload")
    error: str = Field("", description="Field-level error info on failure, empty on success")
    time: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        description="Unix timestamp in milliseconds",
    )


__all__ = ["ApiResponse", "PageData", "PageMeta"]
