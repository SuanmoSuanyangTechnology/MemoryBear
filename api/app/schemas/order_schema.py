"""
Order Schema

Defines request and response models for order operations.
"""

from pydantic import BaseModel, Field
from typing import Any, Optional


class CreateOrderRequest(BaseModel):
    """Create order request model"""
    
    product_id: str = Field(..., description="Product ID")
    quantity: int = Field(..., gt=0, description="Order quantity")
    customer_name: Optional[str] = Field(None, description="Customer name")
    customer_email: Optional[str] = Field(None, description="Customer email")
    notes: Optional[str] = Field(None, description="Order notes")
    
    class Config:
        json_schema_extra = {
            "example": {
                "product_id": "PROD-001",
                "quantity": 2,
                "customer_name": "John Doe",
                "customer_email": "john@example.com",
                "notes": "Please deliver before 5pm"
            }
        }


class OrderResponse(BaseModel):
    """Order response model"""
    
    order_id: str = Field(..., description="Order ID")
    status: str = Field(..., description="Order status")
    product_id: str = Field(..., description="Product ID")
    quantity: int = Field(..., description="Order quantity")
    total_amount: Optional[float] = Field(None, description="Total amount")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    message: Optional[str] = Field(None, description="Response message")
    
    class Config:
        json_schema_extra = {
            "example": {
                "order_id": "ORD-20231224-001",
                "status": "pending",
                "product_id": "PROD-001",
                "quantity": 2,
                "total_amount": 199.99,
                "created_at": "2023-12-24T10:30:00Z",
                "message": "Order created successfully"
            }
        }


class ExternalOrderResponse(BaseModel):
    """External API response model (flexible structure)"""
    
    success: bool = Field(default=True, description="Request success status")
    data: Optional[Any] = Field(None, description="Response data")
    error: Optional[str] = Field(None, description="Error message")
    code: Optional[int] = Field(None, description="Response code")
