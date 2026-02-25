import datetime
import json
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.logging_config import get_api_logger
from app.core.response_utils import success, fail
from app.db import get_db
from app.dependencies import get_current_user
from app.models import mcp_market_model
from app.models.user_model import User
from app.schemas import mcp_market_schema
from app.schemas.response_schema import ApiResponse
from app.services import mcp_market_service

# Obtain a dedicated API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/mcp_markets",
    tags=["mcp_markets"],
    dependencies=[Depends(get_current_user)]  # Apply auth to all routes in this controller
)


@router.get("/mcp_markets", response_model=ApiResponse)
async def get_mcp_markets(
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        orderby: Optional[str] = Query(None, description="Sort fields, such as: category, created_at"),
        desc: Optional[bool] = Query(False, description="Is it descending order"),
        keywords: Optional[str] = Query(None, description="Search keywords (mcp_market base name)"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Query the mcp markets list in pages
    - Support keyword search for name,description
    - Support dynamic sorting
    - Return paging metadata + mcp_market list
    """
    api_logger.info(
        f"Query mcp market list: tenant_id={current_user.tenant_id}, page={page}, pagesize={pagesize}, keywords={keywords}, username: {current_user.username}")

    # 1. parameter validation
    if page < 1 or pagesize < 1:
        api_logger.warning(f"Error in paging parameters: page={page}, pagesize={pagesize}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Construct query conditions
    filters = []

    # Keyword search (fuzzy matching of mcp market name,description)
    if keywords:
        api_logger.debug(f"Add keyword search criteria: {keywords}")
        filters.append(
            or_(
                mcp_market_model.McpMarket.name.ilike(f"%{keywords}%"),
                mcp_market_model.McpMarket.description.ilike(f"%{keywords}%")
            )
        )
    # 3. Execute paged query
    try:
        api_logger.debug("Start executing mcp market paging query")
        total, items = mcp_market_service.get_mcp_markets_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            current_user=current_user
        )
        api_logger.info(f"mcp market query successful: total={total}, returned={len(items)} records")
    except Exception as e:
        api_logger.error(f"mcp market query failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )

    # 4. Return structured response
    result = {
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": True if page * pagesize < total else False
        }
    }
    return success(data=jsonable_encoder(result), msg="Query of mcp market list successful")


@router.post("/mcp_market", response_model=ApiResponse)
async def create_mcp_market(
        create_data: mcp_market_schema.McpMarketCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    create mcp market
    """
    api_logger.info(
        f"Request to create a mcp market: name={create_data.name}, tenant_id={current_user.tenant_id}, username: {current_user.username}")

    try:
        api_logger.debug(f"Start creating the mcp market: {create_data.name}")
        # 1. Check if the mcp market name already exists
        db_mcp_market_exist = mcp_market_service.get_mcp_market_by_name(db, name=create_data.name, current_user=current_user)
        if db_mcp_market_exist:
            api_logger.warning(f"The mcp market name already exists: {create_data.name}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"The mcp market name already exists: {create_data.name}"
            )
        db_mcp_market = mcp_market_service.create_mcp_market(db=db, mcp_market=create_data, current_user=current_user)
        api_logger.info(
            f"The mcp market has been successfully created: {db_mcp_market.name} (ID: {db_mcp_market.id})")
        return success(data=jsonable_encoder(mcp_market_schema.McpMarket.model_validate(db_mcp_market)),
                       msg="The mcp market has been successfully created")
    except Exception as e:
        api_logger.error(f"The creation of the mcp market failed: {create_data.name} - {str(e)}")
        raise


@router.get("/{mcp_market_id}", response_model=ApiResponse)
async def get_mcp_market(
        mcp_market_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Retrieve mcp market information based on mcp_market_id
    """
    api_logger.info(
        f"Obtain details of the mcp market: mcp_market_id={mcp_market_id}, username: {current_user.username}")

    try:
        # 1. Query mcp market information from the database
        api_logger.debug(f"Query mcp market: {mcp_market_id}")
        db_mcp_market = mcp_market_service.get_mcp_market_by_id(db, mcp_market_id=mcp_market_id, current_user=current_user)
        if not db_mcp_market:
            api_logger.warning(f"The mcp market does not exist or access is denied: mcp_market_id={mcp_market_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The mcp market does not exist or access is denied"
            )

        api_logger.info(f"mcp market query successful: {db_mcp_market.name} (ID: {db_mcp_market.id})")
        return success(data=jsonable_encoder(mcp_market_schema.McpMarket.model_validate(db_mcp_market)),
                       msg="Successfully obtained mcp market information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"mcp market query failed: mcp_market_id={mcp_market_id} - {str(e)}")
        raise


@router.put("/{mcp_market_id}", response_model=ApiResponse)
async def update_mcp_market(
        mcp_market_id: uuid.UUID,
        update_data: mcp_market_schema.McpMarketUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # 1. Check if the mcp market exists
    api_logger.debug(f"Query the mcp market to be updated: {mcp_market_id}")
    db_mcp_market = mcp_market_service.get_mcp_market_by_id(db, mcp_market_id=mcp_market_id, current_user=current_user)

    if not db_mcp_market:
        api_logger.warning(
            f"The mcp market does not exist or you do not have permission to access it: mcp_market_id={mcp_market_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The mcp market does not exist or you do not have permission to access it"
        )

    # 2. not updating the name (name already exists)
    update_dict = update_data.dict(exclude_unset=True)
    if "name" in update_dict:
        name = update_dict["name"]
        if name != db_mcp_market.name:
            # Check if the mcp market name already exists
            db_mcp_market_exist = mcp_market_service.get_mcp_market_by_name(db, name=name, current_user=current_user)
            if db_mcp_market_exist:
                api_logger.warning(f"The mcp market name already exists: {name}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"The mcp market name already exists: {name}"
                )
    # 3. Update fields (only update non-null fields)
    api_logger.debug(f"Start updating the mcp market fields: {mcp_market_id}")
    updated_fields = []
    for field, value in update_dict.items():
        if hasattr(db_mcp_market, field):
            old_value = getattr(db_mcp_market, field)
            if old_value != value:
                # update value
                setattr(db_mcp_market, field, value)
                updated_fields.append(f"{field}: {old_value} -> {value}")

    if updated_fields:
        api_logger.debug(f"updated fields: {', '.join(updated_fields)}")

    # 4. Save to database
    try:
        db.commit()
        db.refresh(db_mcp_market)
        api_logger.info(f"The mcp market has been successfully updated: {db_mcp_market.name} (ID: {db_mcp_market.id})")
    except Exception as e:
        db.rollback()
        api_logger.error(f"The mcp market update failed: mcp_market_id={mcp_market_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The mcp market update failed: {str(e)}"
        )

    # 5. Return the updated mcp market
    return success(data=jsonable_encoder(mcp_market_schema.McpMarket.model_validate(db_mcp_market)),
                   msg="The mcp market information updated successfully")


@router.delete("/{mcp_market_id}", response_model=ApiResponse)
async def delete_mcp_market(
        mcp_market_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    delete mcp market
    """
    api_logger.info(f"Request to delete mcp market: mcp_market_id={mcp_market_id}, username: {current_user.username}")

    try:
        # 1. Check whether the mcp market exists
        api_logger.debug(f"Check whether the mcp market exists: {mcp_market_id}")
        db_mcp_market = mcp_market_service.get_mcp_market_by_id(db, mcp_market_id=mcp_market_id, current_user=current_user)

        if not db_mcp_market:
            api_logger.warning(
                f"The mcp market does not exist or you do not have permission to access it: mcp_market_id={mcp_market_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The mcp market does not exist or you do not have permission to access it"
            )

        # 2. Deleting mcp market
        mcp_market_service.delete_mcp_market_by_id(db, mcp_market_id=mcp_market_id, current_user=current_user)
        api_logger.info(f"The mcp market has been successfully deleted: (ID: {mcp_market_id})")
        return success(msg="The mcp market has been successfully deleted")
    except Exception as e:
        api_logger.error(f"Failed to delete from the mcp market: mcp_market_id={mcp_market_id} - {str(e)}")
        raise
