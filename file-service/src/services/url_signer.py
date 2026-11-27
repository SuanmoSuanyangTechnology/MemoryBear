"""Signed URL for local storage; signature-compatible with the monolith
(SECRET_KEY must match, otherwise URLs issued before cutover break)."""
import hashlib
import hmac
import time
from typing import Optional, Tuple
from urllib.parse import urlencode

from ..config import settings


def generate_signed_url(file_id: str, expires: int,
                        base_url: Optional[str] = None) -> str:
    if base_url is None:
        base_url = settings.file_local_server_url
    expires_at = int(time.time()) + expires
    signature = _generate_signature(file_id, expires_at)
    params = urlencode({"expires": expires_at, "signature": signature})
    return f"{base_url}/storage/public/{file_id}?{params}"


def verify_signed_url(file_id: str, expires_at: int,
                      signature: str) -> Tuple[bool, Optional[str]]:
    if time.time() > expires_at:
        return False, "URL has expired"
    if not hmac.compare_digest(signature, _generate_signature(file_id, expires_at)):
        return False, "Invalid signature"
    return True, None


def _generate_signature(file_id: str, expires_at: int) -> str:
    key = settings.secret_key.get_secret_value().encode()
    message = f"{file_id}:{expires_at}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()
