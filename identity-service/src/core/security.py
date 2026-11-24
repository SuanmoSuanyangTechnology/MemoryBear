"""JWT and password hashing primitives (byte-compatible with the monolith).

Claims, algorithm and secret are identical so tokens issued on either side
verify on the other during cutover and rollback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from src.config import settings

pwd_context = CryptContext(schemes=["scrypt"], deprecated="auto")


def create_access_token(
    subject: str | Any, expires_delta: timedelta | None = None
) -> tuple[str, str]:
    now = datetime.now(UTC)
    expire = now + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    token_id = str(uuid.uuid4())
    to_encode = {
        "exp": expire,
        "iat": now,
        "sub": str(subject),
        "type": "access",
        "jti": token_id,
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM), token_id


def create_refresh_token(
    subject: str | Any, expires_delta: timedelta | None = None
) -> tuple[str, str]:
    now = datetime.now(UTC)
    expire = now + (
        expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )
    token_id = str(uuid.uuid4())
    to_encode = {
        "exp": expire,
        "iat": now,
        "sub": str(subject),
        "type": "refresh",
        "jti": token_id,
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM), token_id


def verify_token(token: str, token_type: str = "access") -> str | None:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id: str | None = payload.get("sub")
        if user_id is None or payload.get("type") != token_type:
            return None
        return user_id
    except JWTError:
        return None


def get_token_id(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload.get("jti")
    except JWTError:
        return None


def get_token_expiry(token: str) -> datetime | None:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        exp_timestamp = payload.get("exp")
        if exp_timestamp:
            return datetime.fromtimestamp(exp_timestamp, tz=UTC)
        return None
    except JWTError:
        return None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
