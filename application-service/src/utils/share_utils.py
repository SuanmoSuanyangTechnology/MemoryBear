"""Share-token helpers (frozen port of the host ``app.core.share_utils``).

Only the helpers consumed by the management-plane share faces are ported:
token minting, password hashing and public share-URL building. Password
verification and embed-code generation belong to the public share-view face
and arrive with the runtime plane.
"""

from __future__ import annotations

import hashlib
import secrets
import string

__all__ = ["build_share_url", "generate_share_token", "hash_password"]


def generate_share_token(length: int = 16) -> str:
    """Random share token over letters and digits."""

    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_password(password: str) -> str:
    """Hash a share password as SHA-256 with a random salt (``salt$digest``).

    Host parity: the digest format must stay identical so hashes written by
    either side verify against each other during the coexistence period.
    """

    salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${pwd_hash}"


def build_share_url(share_token: str, base_url: str | None = None) -> str:
    """Build the public release-share URL; relative when no base is given."""

    if base_url:
        return f"{base_url.rstrip('/')}/public/share/{share_token}"
    return f"/public/share/{share_token}"
