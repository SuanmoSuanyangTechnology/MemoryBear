"""Invalidation notifications that trigger identity's snapshot rebuilds.

Message shapes are identical to the monolith's ``invalidation_notify``; only
the sync publisher is provided here because every management-plane route runs
in FastAPI's threadpool. Publish failures are logged and never block callers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from src.config import settings
from src.redis import get_thread_safe_sync_redis

logger = logging.getLogger(__name__)

CHANNEL = settings.AUTH_INVALIDATION_CHANNEL


def api_key_hash(plain: str) -> str:
    """sha256(plaintext) — must match auth_sdk/snapshot.py."""
    return hashlib.sha256(plain.encode()).hexdigest()


def _publish(message: dict[str, Any]) -> None:
    try:
        get_thread_safe_sync_redis().publish(
            CHANNEL, json.dumps(message, ensure_ascii=False)
        )
    except Exception:
        # Diagnostics must not leak secrets: drop the plaintext api key that
        # notify_api_key_created_sync carries in the message body. The publish
        # payload itself is unchanged and still includes the key.
        logger.exception(
            "invalidation publish failed: %s",
            {k: v for k, v in message.items() if k != "key"},
        )


def notify_user_sync(user_id: str) -> None:
    _publish({"kind": "user", "id": str(user_id)})


def notify_api_key_sync(api_key_hash_: str) -> None:
    _publish({"kind": "api_key", "hash": api_key_hash_})


def notify_api_key_created_sync(plain: str) -> None:
    # Plaintext appears only in the message body: identity drops the stale
    # snapshot and rebuilds it straight from the DB.
    _publish({"kind": "api_key", "hash": api_key_hash(plain), "key": plain})


def notify_tenant_sync(tenant_id: str) -> None:
    _publish({"kind": "tenant", "id": str(tenant_id)})
