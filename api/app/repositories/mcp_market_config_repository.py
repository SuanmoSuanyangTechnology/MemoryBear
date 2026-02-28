import uuid
import datetime
from sqlalchemy.orm import Session
from app.models.mcp_market_config_model import McpMarketConfig
from app.schemas import mcp_market_config_schema
from app.core.logging_config import get_db_logger

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


def create_mcp_market_config(db: Session, mcp_market_config: mcp_market_config_schema.McpMarketConfigCreate) -> McpMarketConfig:
    db_logger.debug(f"Create a mcp market config record: mcp_market_id={mcp_market_config.mcp_market_id}")

    try:
        db_mcp_market_config = McpMarketConfig(**mcp_market_config.model_dump())
        db.add(db_mcp_market_config)
        db.commit()
        db_logger.info(f"McpMarketConfig record created successfully: {mcp_market_config.mcp_market_id} (ID: {db_mcp_market_config.id})")
        return db_mcp_market_config
    except Exception as e:
        db_logger.error(f"Failed to create a mcp market config record: mcp_market_id={mcp_market_config.mcp_market_id} - {str(e)}")
        db.rollback()
        raise


def get_mcp_market_config_by_id(db: Session, mcp_market_config_id: uuid.UUID) -> McpMarketConfig | None:
    db_logger.debug(f"Query mcp market config based on ID: mcp_market_config_id={mcp_market_config_id}")

    try:
        db_mcp_market_config = db.query(McpMarketConfig).filter(McpMarketConfig.id == mcp_market_config_id).first()
        if db_mcp_market_config:
            db_logger.debug(f"McpMarketConfig query successful: (ID: {mcp_market_config_id})")
        else:
            db_logger.debug(f"McpMarketConfig does not exist: mcp_market_config_id={mcp_market_config_id}")
        return db_mcp_market_config
    except Exception as e:
        db_logger.error(f"Failed to query the mcp market config based on the ID: {mcp_market_config_id} - {str(e)}")
        raise


def get_mcp_market_config_by_mcp_market_id(db: Session, mcp_market_id: uuid.UUID, tenant_id: uuid.UUID) -> McpMarketConfig | None:
    db_logger.debug(f"Query mcp market config based on mcp_market_id: {mcp_market_id}")

    try:
        db_mcp_market_config = db.query(McpMarketConfig).filter(McpMarketConfig.mcp_market_id == mcp_market_id, McpMarketConfig.tenant_id == tenant_id).first()
        if db_mcp_market_config:
            db_logger.debug(f"McpMarketConfig query successful: (mcp_market_id: {mcp_market_id})")
        else:
            db_logger.debug(f"McpMarketConfig does not exist: mcp_market_id={mcp_market_id}")
        return db_mcp_market_config
    except Exception as e:
        db_logger.error(f"Failed to query the mcp market config based on the mcp_market_id: {mcp_market_id} - {str(e)}")
        raise


def delete_mcp_market_config_by_id(db: Session, mcp_market_config_id: uuid.UUID):
    db_logger.debug(f"Delete McpMarketConfig record: mcp_market_config_id={mcp_market_config_id}")

    try:
        # First, query the mcp market config information for logging purposes
        result = db.query(McpMarketConfig).filter(McpMarketConfig.id == mcp_market_config_id).delete()
        db.commit()

        if result > 0:
            db_logger.info(f"McpMarketConfig record deleted successfully: (ID: {mcp_market_config_id})")
        else:
            db_logger.warning(f"The mcp market config record does not exist, and cannot be deleted: id={mcp_market_config_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete mcp market config record: id={mcp_market_config_id} - {str(e)}")
        db.rollback()
        raise
