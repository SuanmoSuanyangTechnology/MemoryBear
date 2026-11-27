"""Session bookkeeping shared with the monolith.

Keys, value shapes and gating are byte-compatible: ``user_session:{username}``
stores the same JSON object, ``token_blacklist:{jti}`` stores "blacklisted",
and ``user_token_invalidation:{user_id}`` stores an ISO-Z timestamp. Reads are
gated by ENABLE_SINGLE_SESSION exactly like the host; writes that revoke
credentials are NOT gated. Redis failures are logged and degrade to the
monolith's fail-open fallbacks (no session / not blacklisted / write skipped).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from redis.exceptions import RedisError

from src import redis as iredis
from src.config import settings
from src.core.datetime_utils import as_utc_aware, to_iso_z, utcnow

logger = logging.getLogger(__name__)


class SessionService:
    @staticmethod
    def _get_user_session_key(username: str) -> str:
        return f"user_session:{username}"

    @staticmethod
    def _get_token_blacklist_key(token_id: str) -> str:
        return f"token_blacklist:{token_id}"

    @staticmethod
    def _client():
        return iredis.get_thread_safe_sync_redis()

    @staticmethod
    def set_user_active_session(
        username: str, token_id: str, expires_at: datetime
    ) -> None:
        if not settings.ENABLE_SINGLE_SESSION:
            return
        current_time = utcnow()
        expires_at = as_utc_aware(expires_at)
        session_data = {
            "token_id": token_id,
            "created_at": to_iso_z(current_time),
            "expires_at": to_iso_z(expires_at),
        }
        expire_seconds = int((expires_at - current_time).total_seconds())
        if expire_seconds > 0:
            try:
                SessionService._client().set(
                    SessionService._get_user_session_key(username),
                    json.dumps(session_data, ensure_ascii=False),
                    ex=expire_seconds,
                )
            except RedisError as exc:
                logger.error("Redis set错误: %s", exc)

    @staticmethod
    def get_user_active_session(username: str) -> dict[str, Any] | None:
        if not settings.ENABLE_SINGLE_SESSION:
            return None
        try:
            raw = SessionService._client().get(
                SessionService._get_user_session_key(username)
            )
        except RedisError as exc:
            logger.error("Redis get错误: %s", exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return None

    @staticmethod
    def invalidate_old_session(username: str, new_token_id: str) -> None:
        if not settings.ENABLE_SINGLE_SESSION:
            return
        current_session = SessionService.get_user_active_session(username)
        if current_session and current_session.get("token_id") != new_token_id:
            old_token_id = current_session.get("token_id")
            if old_token_id:
                SessionService.blacklist_token(old_token_id)

    @staticmethod
    def blacklist_token(token_id: str, expire_seconds: int | None = None) -> None:
        if expire_seconds is None:
            expire_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        try:
            SessionService._client().set(
                SessionService._get_token_blacklist_key(token_id),
                "blacklisted",
                ex=expire_seconds,
            )
        except RedisError as exc:
            logger.error("Redis set错误: %s", exc)

    @staticmethod
    def is_token_blacklisted(token_id: str) -> bool:
        if not settings.ENABLE_SINGLE_SESSION:
            return False
        try:
            return (
                SessionService._client().get(
                    SessionService._get_token_blacklist_key(token_id)
                )
                is not None
            )
        except RedisError as exc:
            logger.error("Redis get错误: %s", exc)
            return False

    @staticmethod
    def clear_user_session(username: str) -> None:
        try:
            SessionService._client().delete(
                SessionService._get_user_session_key(username)
            )
        except RedisError as exc:
            logger.error("Redis delete错误: %s", exc)

    @staticmethod
    def invalidate_all_user_tokens(user_id: str) -> None:
        expire_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        try:
            SessionService._client().set(
                f"user_token_invalidation:{user_id}",
                to_iso_z(utcnow()),
                ex=expire_seconds,
            )
        except RedisError as exc:
            logger.error("Redis set错误: %s", exc)

    @staticmethod
    def get_user_token_invalidation_time(user_id: str) -> str | None:
        try:
            result = SessionService._client().get(
                f"user_token_invalidation:{user_id}"
            )
        except RedisError as exc:
            logger.error("Redis get错误: %s", exc)
            return None
        return result if result else None
