"""MCP market management service (port of the monolith ``mcp_market_service``).

The repository returns ORM objects; response schema validation belongs to the
route layer. ``get_market`` is the shared lookup reused by the MCP market
config routes.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, DuplicateResourceException
from ..models.mcp_market_model import McpMarket
from ..repositories.mcp_market_repository import McpMarketRepository
from ..schemas.mcp_market_schema import McpMarketCreate, McpMarketUpdate

if TYPE_CHECKING:
    from ..api.dependencies import Principal

logger = logging.getLogger(__name__)


class McpMarketService:
    """MCP market management service."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = McpMarketRepository(db)

    def get_markets_paginated(
        self,
        principal: Principal,
        page: int,
        pagesize: int,
        orderby: str | None = None,
        desc: bool = False,
        keywords: str | None = None,
    ) -> tuple[int, list[McpMarket]]:
        """Paged market list; keyword search matches name and description."""

        filters = []
        if keywords:
            filters.append(
                or_(
                    McpMarket.name.ilike(f"%{keywords}%"),
                    McpMarket.description.ilike(f"%{keywords}%"),
                )
            )
        logger.info(
            "Query mcp market list: actor_id=%s, page=%s, pagesize=%s, keywords=%s",
            principal.actor_id,
            page,
            pagesize,
            keywords,
        )
        return self.repo.get_paginated(filters, page, pagesize, orderby, desc)

    def create_market(self, data: McpMarketCreate, principal: Principal) -> McpMarket:
        """Create a market; the caller becomes its ``created_by`` owner."""

        if self.get_market_by_name(data.name):
            raise DuplicateResourceException(f"The mcp market name already exists: {data.name}")
        market = self.repo.create(data, created_by=principal.actor_id)
        logger.info(
            "The mcp market has been successfully created: %s (ID: %s), creator: %s",
            market.name,
            market.id,
            principal.actor_id,
        )
        return market

    def get_market(self, market_id: uuid.UUID) -> McpMarket | None:
        return self.repo.get_by_id(market_id)

    def get_market_by_name(self, name: str) -> McpMarket | None:
        return self.repo.get_by_name(name)

    def update_market(self, market: McpMarket, data: McpMarketUpdate) -> McpMarket:
        """Apply a partial update to an already-loaded market and commit it.

        The route owns the missing-market copy (it differs between GET and
        PUT/DELETE); the service holds the state rules: rename uniqueness and
        diff-only field assignment.
        """

        update_dict = data.model_dump(exclude_unset=True)
        if "name" in update_dict and update_dict["name"] != market.name:
            name = update_dict["name"]
            if self.get_market_by_name(name):
                raise DuplicateResourceException(f"The mcp market name already exists: {name}")

        for field, value in update_dict.items():
            if hasattr(market, field) and getattr(market, field) != value:
                setattr(market, field, value)

        try:
            self.db.commit()
            self.db.refresh(market)
        except Exception as exc:
            self.db.rollback()
            raise BusinessException(
                f"The mcp market update failed: {exc}",
                BizCode.INTERNAL_ERROR,
                cause=exc,
            ) from exc

        logger.info(
            "The mcp market has been successfully updated: %s (ID: %s)",
            market.name,
            market.id,
        )
        return market

    def delete_market(self, market_id: uuid.UUID) -> None:
        self.repo.delete_by_id(market_id)
        logger.info("The mcp market has been successfully deleted: (ID: %s)", market_id)


__all__ = ["McpMarketService"]
