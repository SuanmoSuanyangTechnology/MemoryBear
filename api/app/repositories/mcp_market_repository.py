import uuid
import datetime
from sqlalchemy.orm import Session
from app.models.mcp_market_model import McpMarket
from app.schemas import mcp_market_schema
from app.core.logging_config import get_db_logger

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


def get_mcp_markets_paginated(
        db: Session,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """
    Paged query mcp market (with filtering and sorting)
    """
    db_logger.debug(
        f"Query mcp market in pages: page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}, filters_count={len(filters)}")

    try:
        query = db.query(McpMarket)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Calculate the total count (for pagination)
        total = query.count()
        db_logger.debug(f"Total number of mcp_market queries: {total}")

        # sort
        if orderby:
            order_attr = getattr(McpMarket, orderby, None)
            if order_attr is not None:
                if desc:
                    query = query.order_by(order_attr.desc())
                else:
                    query = query.order_by(order_attr.asc())
                db_logger.debug(f"sort: {orderby}, desc={desc}")

        # pagination
        items = query.offset((page - 1) * pagesize).limit(pagesize).all()
        db_logger.info(
            f"The mcp market paging query has been successful: total={total}, Number of current page={len(items)}")

        return total, [mcp_market_schema.McpMarket.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(f"Querying mcp_market pagination failed: page={page}, pagesize={pagesize} - {str(e)}")
        raise


def create_mcp_market(db: Session, mcp_market: mcp_market_schema.McpMarketCreate) -> McpMarket:
    db_logger.debug(f"Create a mcp market record: name={mcp_market.name}")

    try:
        db_mcp_market = McpMarket(**mcp_market.model_dump())
        db.add(db_mcp_market)
        db.commit()
        db_logger.info(f"McpMarket record created successfully: {mcp_market.name} (ID: {db_mcp_market.id})")
        return db_mcp_market
    except Exception as e:
        db_logger.error(f"Failed to create a mcp market record: title={mcp_market.name} - {str(e)}")
        db.rollback()
        raise


def get_mcp_market_by_id(db: Session, mcp_market_id: uuid.UUID) -> McpMarket | None:
    db_logger.debug(f"Query mcp market based on ID: mcp_market_id={mcp_market_id}")

    try:
        db_mcp_market = db.query(McpMarket).filter(McpMarket.id == mcp_market_id).first()
        if db_mcp_market:
            db_logger.debug(f"McpMarket query successful: {db_mcp_market.name} (ID: {mcp_market_id})")
        else:
            db_logger.debug(f"McpMarket does not exist: mcp_market_id={mcp_market_id}")
        return db_mcp_market
    except Exception as e:
        db_logger.error(f"Failed to query the mcp market based on the ID: mcp_market_id={mcp_market_id} - {str(e)}")
        raise


def get_mcp_market_by_name(db: Session, name: str) -> McpMarket | None:
    db_logger.debug(f"Query mcp market based on name: name={name}")

    try:
        db_mcp_market = db.query(McpMarket).filter(McpMarket.name == name).first()
        if db_mcp_market:
            db_logger.debug(f"mcp market query successful: {name} (ID: {db_mcp_market.id})")
        else:
            db_logger.debug(f"mcp market does not exist: name={name}")
        return db_mcp_market
    except Exception as e:
        db_logger.error(f"Failed to query the mcp market based on the name: {name} - {str(e)}")
        raise


def delete_mcp_market_by_id(db: Session, mcp_market_id: uuid.UUID):
    db_logger.debug(f"Delete McpMarket record: mcp_market_id={mcp_market_id}")

    try:
        # First, query the mcp market information for logging purposes
        db_mcp_market = db.query(McpMarket).filter(McpMarket.id == mcp_market_id).first()
        if db_mcp_market:
            name = db_mcp_market.name
        else:
            name = "unknown"

        result = db.query(McpMarket).filter(McpMarket.id == mcp_market_id).delete()
        db.commit()

        if result > 0:
            db_logger.info(f"McpMarket record deleted successfully: {name} (ID: {mcp_market_id})")
        else:
            db_logger.warning(f"The mcp market record does not exist, and cannot be deleted: mcp_market_id={mcp_market_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete mcp market record: mcp_market_id={mcp_market_id} - {str(e)}")
        db.rollback()
        raise
