"""MCP market config service (port of the monolith ``mcp_market_config_service``).

Holds the ModelScope probe flow: token validation on create/update, the
tenant-scoped duplicate check, the connectivity probe that decides the stored
``status``, and the ``mcp_count`` write-back of the paged listing.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..integrations.modelscope_mcp import ModelscopeMcpClient
from ..models.mcp_market_config_model import McpMarketConfig
from ..repositories.mcp_market_config_repository import McpMarketConfigRepository
from ..schemas.mcp_market_config_schema import (
    McpMarketConfigCreate,
    McpMarketConfigUpdate,
)
from .mcp_market_service import McpMarketService

if TYPE_CHECKING:
    from ..api.dependencies import Principal

logger = logging.getLogger(__name__)

MARKET_NOT_FOUND_MSG = "The mcp market does not exist or access is denied"


class McpMarketConfigService:
    """MCP market config service."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = McpMarketConfigRepository(db)
        self.markets = McpMarketService(db)
        self.client = ModelscopeMcpClient()

    def get_config(self, mcp_market_config_id: uuid.UUID) -> McpMarketConfig | None:
        return self.repo.get_by_id(mcp_market_config_id)

    def get_config_by_market_id(
        self, mcp_market_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> McpMarketConfig | None:
        return self.repo.get_by_market_id(mcp_market_id, tenant_id)

    def create_config(
        self, data: McpMarketConfigCreate, principal: Principal
    ) -> McpMarketConfig:
        """Validate the token, dedupe per tenant, probe connectivity, persist."""

        if not data.token:
            raise BusinessException(
                "Token is required to access ModelScope MCP market",
                BizCode.INVALID_PARAMETER,
            )
        self.client.validate_token(data.token)

        if self.get_config_by_market_id(data.mcp_market_id, principal.tenant_id):
            raise BusinessException(
                f"The mcp market id already exists: {data.mcp_market_id}",
                BizCode.RESOURCE_ALREADY_EXISTS,
            )

        data.status = 1
        try:
            self.client.list_servers(data.token, 1, 20, "")
        except BusinessException as exc:
            logger.warning(
                "Second MCP probe failed, creating config as disconnected: %s", exc
            )
            data.status = 0

        config = self.repo.create(data, principal.tenant_id, principal.actor_id)
        logger.info(
            "The mcp market config has been successfully created: (ID: %s)",
            config.id,
        )
        return config

    def list_market_servers(
        self,
        token: str,
        page: int,
        pagesize: int,
        keywords: str | None,
    ) -> tuple[int, list[dict[str, Any]]]:
        return self.client.list_servers(token, page, pagesize, keywords)

    def list_operational_servers(self, token: str) -> list[dict[str, Any]]:
        return self.client.list_operational_servers(token)

    def get_server(self, token: str, server_id: str) -> dict[str, Any]:
        return self.client.get_server(token, server_id)

    def sync_market_count(self, mcp_market_id: uuid.UUID, total: int) -> None:
        """Write the listing total back to the owning market."""

        market = self.markets.get_market(mcp_market_id)
        if not market:
            raise BusinessException(MARKET_NOT_FOUND_MSG, BizCode.NOT_FOUND)
        market.mcp_count = total
        self.db.commit()
        self.db.refresh(market)

    def update_config(
        self, config: McpMarketConfig, data: McpMarketConfigUpdate
    ) -> McpMarketConfig:
        """Validate a new token when supplied, then apply the diff and commit."""

        if data.token is not None:
            self.client.validate_token(data.token)

        update_dict = data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            if hasattr(config, field) and getattr(config, field) != value:
                setattr(config, field, value)

        try:
            self.db.commit()
            self.db.refresh(config)
        except Exception as exc:
            self.db.rollback()
            raise BusinessException(
                f"The mcp market config update failed: {exc}",
                BizCode.INTERNAL_ERROR,
                cause=exc,
            ) from exc

        logger.info(
            "The mcp market config has been successfully updated: (ID: %s)",
            config.id,
        )
        return config

    def delete_config(self, mcp_market_config_id: uuid.UUID) -> None:
        self.repo.delete_by_id(mcp_market_config_id)
        logger.info(
            "The mcp market config has been successfully deleted: (ID: %s)",
            mcp_market_config_id,
        )


__all__ = ["McpMarketConfigService"]
