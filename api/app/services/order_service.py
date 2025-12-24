"""
Order Service

Handles order operations including forwarding requests to external APIs.
"""

import logging
import httpx
from typing import Dict, Any, Optional
from app.schemas.order_schema import CreateOrderRequest

logger = logging.getLogger(__name__)


class OrderService:
    """Order service for handling order operations"""
    
    def __init__(self, external_api_url: Optional[str] = None, api_key: Optional[str] = None):
        """Initialize order service
        
        Args:
            external_api_url: External API base URL
            api_key: API key for authentication
        """
        # Default external API URL (replace with actual URL)
        self.external_api_url = external_api_url or "https://api.example.com/v1"
        self.api_key = api_key
        self.timeout = 30.0  # 30 seconds timeout
    
    async def create_order(
        self, 
        order_data: CreateOrderRequest,
        user_id: str
    ) -> Dict[str, Any]:
        """Create order by forwarding request to external API
        
        Args:
            order_data: Order creation data
            user_id: Current user ID
        
        Returns:
            Order response data
        
        Raises:
            httpx.HTTPError: If external API request fails
            Exception: For other errors
        """
        try:
            # Prepare request payload
            payload = {
                "product_id": order_data.product_id,
                "quantity": order_data.quantity,
                "customer_name": order_data.customer_name,
                "customer_email": order_data.customer_email,
                "notes": order_data.notes,
                "user_id": user_id  # Include user ID for tracking
            }
            
            # Prepare headers
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "MemoryBear-OrderService/1.0"
            }
            
            # Add API key if configured
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            logger.info(f"Forwarding order creation request to external API: {self.external_api_url}/orders")
            logger.debug(f"Request payload: {payload}")
            
            # Make async HTTP request to external API
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.external_api_url}/orders",
                    json=payload,
                    headers=headers
                )
                
                # Log response status
                logger.info(f"External API response status: {response.status_code}")
                
                # Raise exception for 4xx/5xx status codes
                response.raise_for_status()
                
                # Parse response
                response_data = response.json()
                logger.debug(f"External API response data: {response_data}")
                
                # Transform external API response to internal format
                return self._transform_external_response(response_data)
        
        except httpx.HTTPStatusError as e:
            logger.error(f"External API returned error status: {e.response.status_code}")
            logger.error(f"Error response: {e.response.text}")
            
            # Try to parse error response
            try:
                error_data = e.response.json()
                error_message = error_data.get("message") or error_data.get("error") or "External API error"
            except Exception:
                error_message = f"External API error: {e.response.status_code}"
            
            raise Exception(f"Failed to create order: {error_message}")
        
        except httpx.TimeoutException:
            logger.error(f"External API request timeout after {self.timeout}s")
            raise Exception("Order creation timeout - external service not responding")
        
        except httpx.RequestError as e:
            logger.error(f"External API request failed: {str(e)}")
            raise Exception(f"Failed to connect to external order service: {str(e)}")
        
        except Exception as e:
            logger.error(f"Unexpected error during order creation: {str(e)}", exc_info=True)
            raise Exception(f"Order creation failed: {str(e)}")
    
    def _transform_external_response(self, external_data: Dict[str, Any]) -> Dict[str, Any]:
        """Transform external API response to internal format
        
        Args:
            external_data: Response data from external API
        
        Returns:
            Transformed response data
        """
        # Handle different response formats from external API
        # Adjust this based on actual external API response structure
        
        if "data" in external_data:
            # Format 1: {"success": true, "data": {...}}
            data = external_data["data"]
        elif "order" in external_data:
            # Format 2: {"order": {...}}
            data = external_data["order"]
        else:
            # Format 3: Direct response
            data = external_data
        
        # Extract fields with fallbacks
        return {
            "order_id": data.get("order_id") or data.get("id") or "UNKNOWN",
            "status": data.get("status") or "pending",
            "product_id": data.get("product_id") or "",
            "quantity": data.get("quantity") or 0,
            "total_amount": data.get("total_amount") or data.get("amount"),
            "created_at": data.get("created_at") or data.get("timestamp"),
            "message": external_data.get("message") or "Order created successfully"
        }
    
    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """Get order details from external API
        
        Args:
            order_id: Order ID
        
        Returns:
            Order details
        """
        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            logger.info(f"Fetching order {order_id} from external API")
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.external_api_url}/orders/{order_id}",
                    headers=headers
                )
                response.raise_for_status()
                return response.json()
        
        except Exception as e:
            logger.error(f"Failed to fetch order {order_id}: {str(e)}")
            raise Exception(f"Failed to fetch order: {str(e)}")


# Singleton instance
_order_service_instance: Optional[OrderService] = None


def get_order_service(
    external_api_url: Optional[str] = None,
    api_key: Optional[str] = None
) -> OrderService:
    """Get order service instance
    
    Args:
        external_api_url: External API URL (optional, uses default if not provided)
        api_key: API key (optional)
    
    Returns:
        OrderService instance
    """
    global _order_service_instance
    
    if _order_service_instance is None:
        _order_service_instance = OrderService(
            external_api_url=external_api_url,
            api_key=api_key
        )
    
    return _order_service_instance
