"""Global pagination response contract compatible with the API service."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PageMeta(BaseModel):
    """Pagination metadata using the API's exact field names and semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(..., ge=1, description="当前页码，从1开始")
    pagesize: int = Field(..., ge=0, description="每页数量")
    total: int = Field(..., ge=0, description="总条数")
    hasnext: bool = Field(..., description="是否有下一页")

    @model_validator(mode="after")
    def validate_hasnext(self) -> PageMeta:
        expected = self.page * self.pagesize < self.total
        if self.hasnext is not expected:
            raise ValueError("hasnext must equal (page * pagesize) < total")
        return self

    @classmethod
    def create(cls, *, page: int, pagesize: int, total: int) -> PageMeta:
        return cls(
            page=page,
            pagesize=pagesize,
            total=total,
            hasnext=page * pagesize < total,
        )


class PageData[ItemT](BaseModel):
    """Standard API pagination payload: metadata under page and rows under items."""

    model_config = ConfigDict(extra="forbid")

    page: PageMeta = Field(..., description="分页元数据")
    items: list[ItemT] = Field(..., description="分页数据列表")

    @classmethod
    def create(
        cls,
        *,
        page: int,
        pagesize: int,
        total: int,
        items: Iterable[ItemT],
    ) -> PageData[ItemT]:
        return cls(
            page=PageMeta.create(page=page, pagesize=pagesize, total=total),
            items=list(items),
        )
