import uuid
from sqlalchemy.orm import Session
from app.models.user_model import User
from app.models.mcp_market_model import McpMarket
from app.schemas.mcp_market_schema import McpMarketCreate, McpMarketUpdate
from app.repositories import mcp_market_repository
from app.core.logging_config import get_business_logger

# Obtain a dedicated logger for business logic
business_logger = get_business_logger()


def get_mcp_markets_paginated(
        db: Session,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(
        f"Query mcp market in pages: username={current_user.username}, page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}")

    try:
        total, items = mcp_market_repository.get_mcp_markets_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc
        )
        business_logger.info(
            f"The mcp market paging query has been successful: username={current_user.username}, total={total}, Number of current page={len(items)}")
        return total, items
    except Exception as e:
        business_logger.error(f"Querying mcp market pagination failed: username={current_user.username} - {str(e)}")
        raise


def create_mcp_market(
        db: Session, mcp_market: McpMarketCreate, current_user: User
) -> McpMarket:
    business_logger.info(f"Create a mcp market base: {mcp_market.name}, creator: {current_user.username}")

    try:
        mcp_market.created_by = current_user.id
        business_logger.debug(f"Start creating the mcp market: {mcp_market.name}")
        db_mcp_market = mcp_market_repository.create_mcp_market(
            db=db, mcp_market=mcp_market
        )
        business_logger.info(
            f"The mcp market has been successfully created: {mcp_market.name} (ID: {db_mcp_market.id}), creator: {current_user.username}")
        return db_mcp_market
    except Exception as e:
        business_logger.error(f"Failed to create a mcp market: {mcp_market.name} - {str(e)}")
        raise


def get_mcp_market_by_id(db: Session, mcp_market_id: uuid.UUID, current_user: User) -> McpMarket | None:
    business_logger.debug(
        f"Query mcp market based on ID: mcp_market_id={mcp_market_id}, username: {current_user.username}")

    try:
        mcpMarket = mcp_market_repository.get_mcp_market_by_id(db=db, mcp_market_id=mcp_market_id)
        if mcpMarket:
            business_logger.info(f"mcp market query successful: {mcpMarket.name} (ID: {mcp_market_id})")
        else:
            business_logger.warning(f"mcp market does not exist: mcp_market_id={mcp_market_id}")
        return mcpMarket
    except Exception as e:
        business_logger.error(
            f"Failed to query the mcp market based on the ID: {mcp_market_id} - {str(e)}")
        raise


def get_mcp_market_by_name(db: Session, name: str, current_user: User) -> McpMarket | None:
    business_logger.debug(f"Query mcp market based on name: name={name}, username: {current_user.username}")

    try:
        db_mcp_market = mcp_market_repository.get_mcp_market_by_name(db=db, name=name)
        if db_mcp_market:
            business_logger.info(f"mcp market query successful: {name} (ID: {db_mcp_market.id})")
        else:
            business_logger.warning(f"mcp market does not exist: name={name}")
        return db_mcp_market
    except Exception as e:
        business_logger.error(f"Failed to query the mcp market based on the name: name={name} - {str(e)}")
        raise


def delete_mcp_market_by_id(db: Session, mcp_market_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete mcp market: mcp_market_id={mcp_market_id}, operator: {current_user.username}")

    try:
        # First, query the mcp market information for logging purposes
        mcpMarket = mcp_market_repository.get_mcp_market_by_id(db=db, mcp_market_id=mcp_market_id)
        if mcpMarket:
            business_logger.debug(f"Execute mcp market deletion: {mcpMarket.name} (ID: {mcp_market_id})")
        else:
            business_logger.warning(f"The mcp market to be deleted does not exist: mcp_market_id={mcp_market_id}")

        mcp_market_repository.delete_mcp_market_by_id(db=db, mcp_market_id=mcp_market_id)
        business_logger.info(
            f"mcp market record deleted successfully: mcp_market_id={mcp_market_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete mcp market: mcp_market_id={mcp_market_id} - {str(e)}")
        raise
