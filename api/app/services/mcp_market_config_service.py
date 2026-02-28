import uuid
from sqlalchemy.orm import Session
from app.models.user_model import User
from app.models.mcp_market_config_model import McpMarketConfig
from app.schemas.mcp_market_config_schema import McpMarketConfigCreate, McpMarketConfigUpdate
from app.repositories import mcp_market_config_repository
from app.core.logging_config import get_business_logger

# Obtain a dedicated logger for business logic
business_logger = get_business_logger()


def create_mcp_market_config(
        db: Session, mcp_market_config: McpMarketConfigCreate, current_user: User
) -> McpMarketConfig:
    business_logger.info(f"Create a mcp market config base: {mcp_market_config.mcp_market_id}, creator: {current_user.username}")

    try:
        mcp_market_config.tenant_id = current_user.tenant_id
        mcp_market_config.created_by = current_user.id
        business_logger.debug(f"Start creating the mcp market config on mcp_market_id: {mcp_market_config.mcp_market_id}")
        db_mcp_market_config = mcp_market_config_repository.create_mcp_market_config(
            db=db, mcp_market_config=mcp_market_config
        )
        business_logger.info(
            f"The mcp market config has been successfully created: {mcp_market_config.mcp_market_id} (ID: {db_mcp_market_config.id}), creator: {current_user.username}")
        return db_mcp_market_config
    except Exception as e:
        business_logger.error(f"Failed to create a mcp marke config: {mcp_market_config.mcp_market_id} - {str(e)}")
        raise


def get_mcp_market_config_by_id(db: Session, mcp_market_config_id: uuid.UUID, current_user: User) -> McpMarketConfig | None:
    business_logger.debug(
        f"Query mcp market config based on ID: mcp_market_config_id={mcp_market_config_id}, username: {current_user.username}")

    try:
        mcpMarketConfig = mcp_market_config_repository.get_mcp_market_config_by_id(db=db, mcp_market_config_id=mcp_market_config_id)
        if mcpMarketConfig:
            business_logger.info(f"mcp market config query successful:  (ID: {mcp_market_config_id})")
        else:
            business_logger.warning(f"mcp market config does not exist: mcp_market_config_id={mcp_market_config_id}")
        return mcpMarketConfig
    except Exception as e:
        business_logger.error(
            f"Failed to query the mcp market config based on the ID: {mcp_market_config_id} - {str(e)}")
        raise


def get_mcp_market_config_by_mcp_market_id(db: Session, mcp_market_id: uuid.UUID, current_user: User) -> McpMarketConfig | None:
    business_logger.debug(
        f"Query mcp market config based on mcp_market_id: {mcp_market_id}, username: {current_user.username}")

    try:
        mcpMarketConfig = mcp_market_config_repository.get_mcp_market_config_by_mcp_market_id(db=db, mcp_market_id=mcp_market_id, tenant_id=current_user.tenant_id)
        if mcpMarketConfig:
            business_logger.info(f"mcp market config query successful:  (mcp_market_id: {mcp_market_id})")
        else:
            business_logger.warning(f"mcp market config does not exist: mcp_market_id={mcp_market_id}")
        return mcpMarketConfig
    except Exception as e:
        business_logger.error(
            f"Failed to query the mcp market config based on the mcp_market_id: {mcp_market_id} - {str(e)}")
        raise


def delete_mcp_market_config_by_id(db: Session, mcp_market_config_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete mcp market config: mcp_market_config_id={mcp_market_config_id}, operator: {current_user.username}")

    try:
        # First, query the mcp market config information for logging purposes
        mcpMarketConfig = mcp_market_config_repository.get_mcp_market_config_by_id(db=db, mcp_market_config_id=mcp_market_config_id)
        if mcpMarketConfig:
            business_logger.debug(f"Execute mcp market config deletion: (ID: {mcp_market_config_id})")
        else:
            business_logger.warning(f"The mcp market config to be deleted does not exist: mcp_market_config_id={mcp_market_config_id}")

        mcp_market_config_repository.delete_mcp_market_config_by_id(db=db, mcp_market_config_id=mcp_market_config_id)
        business_logger.info(
            f"mcp market config record deleted successfully: mcp_market_config_id={mcp_market_config_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete mcp market config: mcp_market_config_id={mcp_market_config_id} - {str(e)}")
        raise
