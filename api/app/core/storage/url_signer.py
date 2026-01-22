"""
URL signing utilities for local file storage.

This module provides functions to generate and verify signed URLs
with expiration time for local file access.
"""

import hashlib
import hmac
import time
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

from app.core.config import settings


def generate_signed_url(
    file_id: str,
    expires: int,
    base_url: Optional[str] = None,
) -> str:
    """
    Generate a signed URL for local file access.

    Args:
        file_id: The file UUID as string.
        expires: URL validity period in seconds.
        base_url: Base URL prefix (default: http://localhost:8000/api).

    Returns:
        A signed URL with expiration timestamp and signature.

    Example:
        >>> generate_signed_url("abc-123", 3600)
        'http://localhost:8000/api/storage/public/abc-123?expires=1234567890&signature=xxx'
    """
    if base_url is None:
        # Use SERVER_IP or default to localhost
        server_url = f"http://{settings.SERVER_IP}:8000/api"
        base_url = server_url

    # Calculate expiration timestamp
    expires_at = int(time.time()) + expires

    # Generate signature
    signature = _generate_signature(file_id, expires_at)

    # Build URL with query parameters
    params = urlencode({
        "expires": expires_at,
        "signature": signature,
    })

    return f"{base_url}/storage/public/{file_id}?{params}"


def verify_signed_url(
    file_id: str,
    expires_at: int,
    signature: str,
) -> Tuple[bool, Optional[str]]:
    """
    Verify a signed URL.

    Args:
        file_id: The file UUID as string.
        expires_at: The expiration timestamp.
        signature: The signature to verify.

    Returns:
        A tuple of (is_valid, error_message).
        If valid, error_message is None.
    """
    # Check expiration
    if time.time() > expires_at:
        return False, "URL has expired"

    # Verify signature
    expected_signature = _generate_signature(file_id, expires_at)
    if not hmac.compare_digest(signature, expected_signature):
        return False, "Invalid signature"

    return True, None


def _generate_signature(file_id: str, expires_at: int) -> str:
    """
    Generate HMAC signature for URL parameters.

    Args:
        file_id: The file UUID as string.
        expires_at: The expiration timestamp.

    Returns:
        Hex-encoded HMAC-SHA256 signature.
    """
    secret_key = settings.SECRET_KEY.encode()
    message = f"{file_id}:{expires_at}".encode()

    signature = hmac.new(secret_key, message, hashlib.sha256).hexdigest()
    return signature
