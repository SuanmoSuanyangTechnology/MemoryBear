"""Authentication middleware"""
from fastapi import Header, HTTPException, status

from app.config import get_config


async def verify_api_key(x_api_key: str = Header(..., alias="X-Api-Key")):
    """Verify API key from request header"""
    config = get_config()
    if x_api_key != config.app.key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return x_api_key
