from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
import os

from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.order_schema import CreateOrderRequest
from app.schemas.response_schema import ApiResponse
from app.services.order_service import get_order_service
from app.core.logging_config import get_api_logger
from app.core.response_utils import success, error

# Get API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/order",
    tags=["Order"],
)


@router.post("", response_model=ApiResponse)
async def create_order(
    order_data: CreateOrderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    try:
        api_logger.info(f"User {current_user.id} creating order for product {order_data.product_id}")
        
        # Get external API configuration from environment
        external_api_url = os.getenv("EXTERNAL_ORDER_API_URL")
        api_key = os.getenv("EXTERNAL_ORDER_API_KEY")
        
        # Get order service instance
        order_service = get_order_service(
            external_api_url=external_api_url,
            api_key=api_key
        )
        
        # Forward request to external API
        result = await order_service.create_order(
            order_data=order_data,
            user_id=str(current_user.id)
        )
        
        api_logger.info(f"Order created successfully: {result.get('order_id')}")
        
        return success(data=result, msg="Order created successfully")
    
    except Exception as e:
        api_logger.error(f"Failed to create order: {str(e)}", exc_info=True)
        return error(msg=str(e), code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/{order_id}", response_model=ApiResponse)
async def get_order(
    order_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get order details from external API
    
    Args:
        order_id: Order ID
        db: Database session
        current_user: Current authenticated user
    
    Returns:
        API response with order details
    """
    try:
        api_logger.info(f"User {current_user.id} fetching order {order_id}")
        
        # Get external API configuration
        external_api_url = os.getenv("EXTERNAL_ORDER_API_URL")
        api_key = os.getenv("EXTERNAL_ORDER_API_KEY")
        
        # Get order service instance
        order_service = get_order_service(
            external_api_url=external_api_url,
            api_key=api_key
        )
        
        # Fetch order from external API
        result = await order_service.get_order(order_id)
        
        api_logger.info(f"Order {order_id} fetched successfully")
        
        return success(data=result, msg="Order fetched successfully")
    
    except Exception as e:
        api_logger.error(f"Failed to fetch order {order_id}: {str(e)}", exc_info=True)
        return error(msg=str(e), code=status.HTTP_500_INTERNAL_SERVER_ERROR)

