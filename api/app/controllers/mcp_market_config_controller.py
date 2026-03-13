import datetime
import json
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.encoders import jsonable_encoder
import requests
from sqlalchemy import or_
from sqlalchemy.orm import Session
from modelscope.hub.errors import raise_for_http_status
from modelscope.hub.mcp_api import MCPApi

from app.core.logging_config import get_api_logger
from app.core.response_utils import success, fail
from app.db import get_db
from app.dependencies import get_current_user
from app.models import mcp_market_config_model
from app.models.user_model import User
from app.schemas import mcp_market_config_schema
from app.schemas.response_schema import ApiResponse
from app.services import mcp_market_config_service, mcp_market_service

# Obtain a dedicated API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/mcp_market_configs",
    tags=["mcp_market_configs"],
    dependencies=[Depends(get_current_user)]  # Apply auth to all routes in this controller
)


@router.get("/mcp_servers", response_model=ApiResponse)
async def get_mcp_servers(
        mcp_market_config_id: uuid.UUID,
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        keywords: Optional[str] = Query(None, description="Search keywords (Optional search query string,e.g. Chinese service name, English service name, author/owner username)"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Query the mcp servers list in pages
    - Support keyword search for name,author,owner
    - Return paging metadata + mcp server list
    """
    api_logger.info(
        f"Query mcp server list: tenant_id={current_user.tenant_id}, page={page}, pagesize={pagesize}, keywords={keywords}, username: {current_user.username}")

    # 1. parameter validation
    if page < 1 or pagesize < 1:
        api_logger.warning(f"Error in paging parameters: page={page}, pagesize={pagesize}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Query mcp market config information from the database
    api_logger.debug(f"Query mcp market config: {mcp_market_config_id}")
    db_mcp_market_config = mcp_market_config_service.get_mcp_market_config_by_id(db,
                                                                                 mcp_market_config_id=mcp_market_config_id,
                                                                                 current_user=current_user)
    if not db_mcp_market_config:
        api_logger.warning(
            f"The mcp market config does not exist or access is denied: mcp_market_config_id={mcp_market_config_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The mcp market config does not exist or access is denied"
        )

    # 3. Execute paged query
    try:
        api = MCPApi()
        token = db_mcp_market_config.token
        api.login(token)

        body = {
            'filter': {},
            'page_number': page,
            'page_size': pagesize,
            'search': keywords
        }
        cookies = api.get_cookies(token)
        r = api.session.put(
            url=api.mcp_base_url,
            headers=api.builder_headers(api.headers),
            json=body,
            cookies=cookies)
        raise_for_http_status(r)
    except requests.exceptions.RequestException as e:
        api_logger.error(f"Failed to get MCP servers: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get MCP servers: {str(e)}"
        )

    data = api._handle_response(r)
    total = data.get('total_count', 0)
    mcp_server_list = data.get('mcp_server_list', [])
    # items = [{
    #     'name': item.get('name', ''),
    #     'id': item.get('id', ''),
    #     'description': item.get('description', '')
    # } for item in mcp_server_list]

    # 4. Return structured response
    result = {
        "items": mcp_server_list,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": True if page * pagesize < total else False
        }
    }
    # 5. Update mck_market.mcp_count
    db_mcp_market = mcp_market_service.get_mcp_market_by_id(db, mcp_market_id=db_mcp_market_config.mcp_market_id, current_user=current_user)
    if not db_mcp_market:
        api_logger.warning(f"The mcp market does not exist or access is denied: mcp_market_id={db_mcp_market_config.mcp_market_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The mcp market does not exist or access is denied"
        )
    db_mcp_market.mcp_count = total
    db.commit()
    db.refresh(db_mcp_market)
    return success(data=result, msg="Query of mcp servers list successful")


@router.get("/operational_mcp_servers", response_model=ApiResponse)
async def get_operational_mcp_servers(
        mcp_market_config_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Query the operational mcp servers list in pages
    - Support keyword search for name,author,owner
    - Return paging metadata + operational mcp server list
    """
    api_logger.info(
        f"Query operational mcp server list: tenant_id={current_user.tenant_id}, username: {current_user.username}")

    # 1. Query mcp market config information from the database
    api_logger.debug(f"Query mcp market config: {mcp_market_config_id}")
    db_mcp_market_config = mcp_market_config_service.get_mcp_market_config_by_id(db,
                                                                                 mcp_market_config_id=mcp_market_config_id,
                                                                                 current_user=current_user)
    if not db_mcp_market_config:
        api_logger.warning(
            f"The mcp market config does not exist or access is denied: mcp_market_config_id={mcp_market_config_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The mcp market config does not exist or access is denied"
        )

    # 2. Execute paged query
    api = MCPApi()
    token = db_mcp_market_config.token
    api.login(token)

    url = f'{api.mcp_base_url}/operational'
    headers = api.builder_headers(api.headers)

    try:
        cookies = api.get_cookies(access_token=token, cookies_required=True)
        r = api.session.get(url, headers=headers, cookies=cookies)
        raise_for_http_status(r)
    except requests.exceptions.RequestException as e:
        api_logger.error(f"Failed to get operational MCP servers: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get operational MCP servers: {str(e)}"
        )

    data = api._handle_response(r)
    total = data.get('total_count', 0)
    mcp_server_list = data.get('mcp_server_list', [])
    # items = [{
    #     'name': item.get('name', ''),
    #     'id': item.get('id', ''),
    #     'description': item.get('description', '')
    # } for item in mcp_server_list]

    # 3. Return structured response
    return success(data=mcp_server_list, msg="Query of operational mcp servers list successful")


@router.get("/mcp_server", response_model=ApiResponse)
async def get_mcp_server(
        mcp_market_config_id: uuid.UUID,
        server_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Get detailed information for a specific MCP Server
    """
    api_logger.info(
        f"Query mcp server: tenant_id={current_user.tenant_id}, mcp_market_config_id={mcp_market_config_id}, server_id={server_id}, username: {current_user.username}")

    # 1. Query mcp market config information from the database
    api_logger.debug(f"Query mcp market config: {mcp_market_config_id}")
    db_mcp_market_config = mcp_market_config_service.get_mcp_market_config_by_id(db,
                                                                                 mcp_market_config_id=mcp_market_config_id,
                                                                                 current_user=current_user)
    if not db_mcp_market_config:
        api_logger.warning(
            f"The mcp market config does not exist or access is denied: mcp_market_config_id={mcp_market_config_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The mcp market config does not exist or access is denied"
        )

    # 2. Get detailed information for a specific MCP Server
    api = MCPApi()
    token = db_mcp_market_config.token
    api.login(token)

    result = api.get_mcp_server(server_id=server_id)
    return success(data=result, msg="Query of mcp servers list successful")


@router.post("/mcp_market_config", response_model=ApiResponse)
async def create_mcp_market_config(
        create_data: mcp_market_config_schema.McpMarketConfigCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    create mcp market config
    """
    api_logger.info(
        f"Request to create a mcp market config: mcp_market_id={create_data.mcp_market_id}, tenant_id={current_user.tenant_id}, username: {current_user.username}")

    try:
        api_logger.debug(f"Start creating the mcp market config: {create_data.mcp_market_id}")
        # 1. Check if the mcp market name already exists
        db_mcp_market_config_exist = mcp_market_config_service.get_mcp_market_config_by_mcp_market_id(db, mcp_market_id=create_data.mcp_market_id, current_user=current_user)
        if db_mcp_market_config_exist:
            api_logger.warning(f"The mcp market id already exists: {create_data.mcp_market_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"The mcp market id already exists: {create_data.mcp_market_id}"
            )
        # 2. verify token
        create_data.status = 1
        try:
            api = MCPApi()
            token = create_data.token
            api.login(token)

            body = {
                'filter': {},
                'page_number': 1,
                'page_size': 20,
                'search': ""
            }
            cookies = api.get_cookies(token)
            r = api.session.put(
                url=api.mcp_base_url,
                headers=api.builder_headers(api.headers),
                json=body,
                cookies=cookies)
            raise_for_http_status(r)
        except requests.exceptions.RequestException as e:
            api_logger.error(f"Failed to get MCP servers: {str(e)}")
            create_data.status = 0
        # 3. create mcp_market_config
        db_mcp_market_config = mcp_market_config_service.create_mcp_market_config(db=db, mcp_market_config=create_data, current_user=current_user)
        api_logger.info(
            f"The mcp market config has been successfully created: (ID: {db_mcp_market_config.id})")
        return success(data=jsonable_encoder(mcp_market_config_schema.McpMarketConfig.model_validate(db_mcp_market_config)),
                       msg="The mcp market config has been successfully created")
    except Exception as e:
        api_logger.error(f"The creation of the mcp market config failed: {create_data.mcp_market_id} - {str(e)}")
        raise


@router.get("/{mcp_market_config_id}", response_model=ApiResponse)
async def get_mcp_market_config(
        mcp_market_config_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Retrieve mcp market config information based on mcp_market_config_id
    """
    api_logger.info(
        f"Obtain details of the mcp market config: mcp_market_config_id={mcp_market_config_id}, username: {current_user.username}")

    try:
        # 1. Query mcp market config information from the database
        api_logger.debug(f"Query mcp market config: {mcp_market_config_id}")
        db_mcp_market_config = mcp_market_config_service.get_mcp_market_config_by_id(db, mcp_market_config_id=mcp_market_config_id, current_user=current_user)
        if not db_mcp_market_config:
            api_logger.warning(f"The mcp market config does not exist or access is denied: mcp_market_config_id={mcp_market_config_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The mcp market config does not exist or access is denied"
            )

        api_logger.info(f"mcp market config query successful: (ID: {db_mcp_market_config.id})")
        return success(data=jsonable_encoder(mcp_market_config_schema.McpMarketConfig.model_validate(db_mcp_market_config)),
                       msg="Successfully obtained mcp market config information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"mcp market config query failed: mcp_market_config_id={mcp_market_config_id} - {str(e)}")
        raise


@router.get("/mcp_market_id/{mcp_market_id}", response_model=ApiResponse)
async def get_mcp_market_config_by_mcp_market_id(
        mcp_market_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Retrieve mcp market config information based on mcp_market_id
    """
    api_logger.info(
        f"Request to create a mcp market config: mcp_market_id={mcp_market_id}, tenant_id={current_user.tenant_id}, username: {current_user.username}")

    try:
        # 1. Query mcp market config information from the database
        api_logger.debug(f"Query mcp market config: mcp_market_id={mcp_market_id}")
        db_mcp_market_config = mcp_market_config_service.get_mcp_market_config_by_mcp_market_id(db, mcp_market_id=mcp_market_id, current_user=current_user)
        if not db_mcp_market_config:
            api_logger.warning(f"The mcp market config does not exist or access is denied: mcp_market_id={mcp_market_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The mcp market config does not exist or access is denied"
            )

        api_logger.info(f"mcp market config query successful: (ID: {db_mcp_market_config.id})")
        return success(data=jsonable_encoder(mcp_market_config_schema.McpMarketConfig.model_validate(db_mcp_market_config)),
                       msg="Successfully obtained mcp market config information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"mcp market config query failed: mcp_market_id={mcp_market_id} - {str(e)}")
        raise


@router.put("/{mcp_market_config_id}", response_model=ApiResponse)
async def update_mcp_market_config(
        mcp_market_config_id: uuid.UUID,
        update_data: mcp_market_config_schema.McpMarketConfigUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # 1. Check if the mcp market config exists
    api_logger.debug(f"Query the mcp market config to be updated: {mcp_market_config_id}")
    db_mcp_market_config = mcp_market_config_service.get_mcp_market_config_by_id(db, mcp_market_config_id=mcp_market_config_id, current_user=current_user)

    if not db_mcp_market_config:
        api_logger.warning(
            f"The mcp market config does not exist or you do not have permission to access it: mcp_market_config_id={mcp_market_config_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The mcp market config does not exist or you do not have permission to access it"
        )

    # 2. Update fields (only update non-null fields)
    api_logger.debug(f"Start updating the mcp market config fields: {mcp_market_config_id}")
    update_dict = update_data.dict(exclude_unset=True)
    updated_fields = []
    for field, value in update_dict.items():
        if hasattr(db_mcp_market_config, field):
            old_value = getattr(db_mcp_market_config, field)
            if old_value != value:
                # update value
                setattr(db_mcp_market_config, field, value)
                updated_fields.append(f"{field}: {old_value} -> {value}")

    if updated_fields:
        api_logger.debug(f"updated fields: {', '.join(updated_fields)}")

    # 3. verify token
    db_mcp_market_config.status = 1
    try:
        api = MCPApi()
        token = update_data.token
        api.login(token)

        body = {
            'filter': {},
            'page_number': 1,
            'page_size': 20,
            'search': ""
        }
        cookies = api.get_cookies(token)
        r = api.session.put(
            url=api.mcp_base_url,
            headers=api.builder_headers(api.headers),
            json=body,
            cookies=cookies)
        raise_for_http_status(r)
    except requests.exceptions.RequestException as e:
        api_logger.error(f"Failed to get MCP servers: {str(e)}")
        db_mcp_market_config.status = 0

    # 4. Save to database
    try:
        db.commit()
        db.refresh(db_mcp_market_config)
        api_logger.info(f"The mcp market config has been successfully updated: (ID: {db_mcp_market_config.id})")
    except Exception as e:
        db.rollback()
        api_logger.error(f"The mcp market config update failed: mcp_market_config_id={mcp_market_config_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The mcp market config update failed: {str(e)}"
        )

    # 5. Return the updated mcp market config
    return success(data=jsonable_encoder(mcp_market_config_schema.McpMarketConfig.model_validate(db_mcp_market_config)),
                   msg="The mcp market config information updated successfully")


@router.delete("/{mcp_market_config_id}", response_model=ApiResponse)
async def delete_mcp_market_config(
        mcp_market_config_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    delete mcp market config
    """
    api_logger.info(f"Request to delete mcp market config: mcp_market_config_id={mcp_market_config_id}, username: {current_user.username}")

    try:
        # 1. Check whether the mcp market config exists
        api_logger.debug(f"Check whether the mcp market config exists: {mcp_market_config_id}")
        db_mcp_market_config = mcp_market_config_service.get_mcp_market_config_by_id(db, mcp_market_config_id=mcp_market_config_id, current_user=current_user)

        if not db_mcp_market_config:
            api_logger.warning(
                f"The mcp market config does not exist or you do not have permission to access it: mcp_market_config_id={mcp_market_config_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The mcp market config does not exist or you do not have permission to access it"
            )

        # 2. Deleting mcp market config
        mcp_market_config_service.delete_mcp_market_config_by_id(db, mcp_market_config_id=mcp_market_config_id, current_user=current_user)
        api_logger.info(f"The mcp market config has been successfully deleted: (ID: {mcp_market_config_id})")
        return success(msg="The mcp market config has been successfully deleted")
    except Exception as e:
        api_logger.error(f"Failed to delete from the mcp market config: mcp_market_config_id={mcp_market_config_id} - {str(e)}")
        raise
