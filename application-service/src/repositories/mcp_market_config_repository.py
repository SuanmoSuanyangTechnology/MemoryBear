"""MCP market config table access (port of the monolith repository).

Returns ORM objects; response schema validation happens in the route layer.
``get_by_id`` keeps the host's tenant-blind read (parity legacy), while the
by-market lookup is tenant-scoped and backs both the duplicate check and the
``/mcp_market_id/{mcp_market_id}`` route.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..models.mcp_market_config_model import McpMarketConfig
from ..schemas.mcp_market_config_schema import McpMarketConfigCreate


class McpMarketConfigRepository:
    """MCP market config repository."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        data: McpMarketConfigCreate,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> McpMarketConfig:
        config = McpMarketConfig(
            **data.model_dump(exclude={"tenant_id", "created_by"}),
            tenant_id=tenant_id,
            created_by=created_by,
        )
        try:
            self.db.add(config)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return config

    def get_by_id(self, mcp_market_config_id: uuid.UUID) -> McpMarketConfig | None:
        return (
            self.db.query(McpMarketConfig)
            .filter(McpMarketConfig.id == mcp_market_config_id)
            .first()
        )

    def get_by_market_id(
        self, mcp_market_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> McpMarketConfig | None:
        return (
            self.db.query(McpMarketConfig)
            .filter(
                McpMarketConfig.mcp_market_id == mcp_market_id,
                McpMarketConfig.tenant_id == tenant_id,
            )
            .first()
        )

    def delete_by_id(self, mcp_market_config_id: uuid.UUID) -> None:
        try:
            self.db.query(McpMarketConfig).filter(
                McpMarketConfig.id == mcp_market_config_id
            ).delete()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise


__all__ = ["McpMarketConfigRepository"]
