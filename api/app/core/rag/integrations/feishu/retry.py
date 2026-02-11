"""Retry strategy for Feishu API calls."""

import asyncio
import functools
from typing import Callable, TypeVar
import httpx

from app.core.rag.integrations.feishu.exceptions import (
    FeishuAuthError,
    FeishuPermissionError,
    FeishuNotFoundError,
    FeishuRateLimitError,
    FeishuNetworkError,
    FeishuDataError,
    FeishuAPIError,
)

T = TypeVar('T')


class RetryStrategy:
    """Retry strategy for API calls."""
    
    # Retryable error types
    RETRYABLE_ERRORS = (
        FeishuNetworkError,
        FeishuRateLimitError,
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.ReadError,
    )
    
    # Non-retryable error types
    NON_RETRYABLE_ERRORS = (
        FeishuAuthError,
        FeishuPermissionError,
        FeishuNotFoundError,
        FeishuDataError,
    )
    
    # Retry configuration
    MAX_RETRIES = 3
    BACKOFF_DELAYS = [1, 2, 4]  # seconds
    
    @classmethod
    def is_retryable(cls, error: Exception) -> bool:
        """Check if an error is retryable."""
        # Check for specific retryable errors
        if isinstance(error, cls.RETRYABLE_ERRORS):
            return True
        
        # Check for non-retryable errors
        if isinstance(error, cls.NON_RETRYABLE_ERRORS):
            return False
        
        # Check for HTTP status codes
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code
            # Retry on 429 (rate limit), 503 (service unavailable), 502 (bad gateway)
            if status_code in [429, 502, 503]:
                return True
            # Don't retry on 4xx errors (except 429)
            if 400 <= status_code < 500:
                return False
            # Retry on 5xx errors
            if 500 <= status_code < 600:
                return True
        
        # Check for FeishuAPIError with specific codes
        if isinstance(error, FeishuAPIError):
            if error.error_code:
                # Rate limit error codes
                if error.error_code in ["99991400", "99991401"]:
                    return True
        
        return False
    
    @classmethod
    async def execute_with_retry(
        cls,
        func: Callable[..., T],
        *args,
        **kwargs
    ) -> T:
        """
        Execute a function with retry logic.
        
        Args:
            func: Async function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            Function result
            
        Raises:
            Exception: The last exception if all retries fail
        """
        last_exception = None
        
        for attempt in range(cls.MAX_RETRIES + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                # Don't retry if not retryable
                if not cls.is_retryable(e):
                    raise
                
                # Don't retry if this was the last attempt
                if attempt >= cls.MAX_RETRIES:
                    raise
                
                # Wait before retrying
                delay = cls.BACKOFF_DELAYS[attempt] if attempt < len(cls.BACKOFF_DELAYS) else cls.BACKOFF_DELAYS[-1]
                await asyncio.sleep(delay)
        
        # Should not reach here, but raise last exception if we do
        if last_exception:
            raise last_exception


def with_retry(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to add retry logic to async functions.
    
    Usage:
        @with_retry
        async def my_api_call():
            ...
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await RetryStrategy.execute_with_retry(func, *args, **kwargs)
    
    return wrapper
