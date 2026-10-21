"""Share access-token codec (port of the host ``auth_service`` token helpers).

A share access token is a non-expiring HS256 JWT binding a viewer id to one
share token. Signing uses the deployment ``SECRET_KEY`` — the same env value
the monolith reads — so tokens minted on either side verify on both during the
coexistence period.
"""

from __future__ import annotations

import time

from jose import JWTError, jwt

from ..config import current_settings
from ..errors import BizCode, BusinessException

TOKEN_ALGORITHM = "HS256"


def _secret_key() -> str:
    return current_settings().secret_key.get_secret_value()


def create_access_token(user_id: str, share_token: str) -> str:
    """Mint a share access token.

    Deliberately no expiry: the token stays valid as long as its share token
    does, matching the host behavior.
    """

    payload = {
        "user_id": user_id,
        "share_token": share_token,
        "iat": int(time.time()),  # signing time
    }

    token = jwt.encode(payload, _secret_key(), algorithm=TOKEN_ALGORITHM)
    return token


def decode_access_token(token: str) -> dict:
    """Decode a share access token into ``{user_id, share_token}``.

    Any malformed/tampered token raises ``BusinessException`` with
    ``BizCode.TOKEN_INVALID`` (host parity).
    """

    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[TOKEN_ALGORITHM])
        return {
            "user_id": payload["user_id"],
            "share_token": payload["share_token"],
        }
    except JWTError as e:
        raise BusinessException("无效的token", BizCode.TOKEN_INVALID, cause=e) from e


__all__ = ["TOKEN_ALGORITHM", "create_access_token", "decode_access_token"]
