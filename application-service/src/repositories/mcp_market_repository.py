"""MCP market table access (port of the monolith ``mcp_market_repository``).

Returns ORM objects; response schema validation happens in the route layer.
Reads keep the host's global scope: ``mcp_markets`` has no tenant column, so no
tenant condition is applied (host parity).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..models.mcp_market_model import McpMarket
from ..schemas.mcp_market_schema import McpMarketCreate


class McpMarketRepository:
    """MCP market repository."""

    def __init__(self, db: Session):
        self.db = db

    def get_paginated(
        self,
        filters: list[ColumnElement[bool]],
        page: int,
        pagesize: int,
        orderby: str | None = None,
        desc: bool = False,
    ) -> tuple[int, list[McpMarket]]:
        """Paged query with filtering and sorting; returns (total, ORM rows)."""

        query = self.db.query(McpMarket)
        for condition in filters:
            query = query.filter(condition)

        total = query.count()

        if orderby:
            order_attr = getattr(McpMarket, orderby, None)
            if order_attr is not None:
                query = query.order_by(order_attr.desc() if desc else order_attr.asc())

        items = query.offset((page - 1) * pagesize).limit(pagesize).all()
        return total, list(items)

    def create(self, data: McpMarketCreate, created_by: uuid.UUID) -> McpMarket:
        market = McpMarket(
            **data.model_dump(exclude={"created_by"}),
            created_by=created_by,
        )
        try:
            self.db.add(market)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return market

    def get_by_id(self, market_id: uuid.UUID) -> McpMarket | None:
        return self.db.query(McpMarket).filter(McpMarket.id == market_id).first()

    def get_by_name(self, name: str) -> McpMarket | None:
        return self.db.query(McpMarket).filter(McpMarket.name == name).first()

    def delete_by_id(self, market_id: uuid.UUID) -> None:
        try:
            self.db.query(McpMarket).filter(McpMarket.id == market_id).delete()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise


__all__ = ["McpMarketRepository"]
