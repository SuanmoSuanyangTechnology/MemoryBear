"""Share-token helpers (frozen port of the host ``app.core.share_utils``).

Token minting, password hashing/verification, public share-URL building and
embed-code generation — shared by the management face and the public
share-view face.
"""

from __future__ import annotations

import hashlib
import secrets
import string

__all__ = [
    "build_share_url",
    "generate_embed_code",
    "generate_share_token",
    "hash_password",
    "verify_password",
]


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


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a share password against its ``salt$digest`` hash."""

    try:
        salt, pwd_hash = hashed_password.split('$')
        computed_hash = hashlib.sha256((plain_password + salt).encode()).hexdigest()
        return computed_hash == pwd_hash
    except (ValueError, AttributeError):
        return False


def build_share_url(share_token: str, base_url: str | None = None) -> str:
    """Build the public release-share URL; relative when no base is given."""

    if base_url:
        return f"{base_url.rstrip('/')}/public/share/{share_token}"
    return f"/public/share/{share_token}"


def generate_embed_code(
    share_token: str,
    width: str = "100%",
    height: str = "600px",
    base_url: str | None = None,
) -> dict:
    """Build the iframe embed payload for a public share link."""

    preview_url = build_share_url(share_token, base_url)

    # Line-trailing spaces are part of the host template and are reproduced
    # byte-for-byte (explicit "\n"/spaces keep the edit tool from trimming them).
    iframe_code = (
        f'<iframe \n'
        f'    src="{preview_url}" \n'
        f'    width="{width}" \n'
        f'    height="{height}" \n'
        f'    frameborder="0" \n'
        f'    allowfullscreen>\n'
        f'</iframe>'
    )

    return {
        "iframe_code": iframe_code,
        "preview_url": preview_url,
        "width": width,
        "height": height
    }
