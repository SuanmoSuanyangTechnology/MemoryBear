"""Staging cache for the DSL/workflow import flow (shared with the monolith).

``temp_id`` keys are the bare ``uuid4().hex`` values minted at upload time: the
monolith stages the same payload under the same naked key during the
coexistence period, so the service must not apply its own ``app:`` namespace
here (same Redis instance and database). Values are JSON strings with
non-ASCII characters kept literal — the exact wire format the monolith's
``aio_redis_set`` produces. Transport failures are swallowed and logged like
the monolith helpers; a lost payload surfaces later as the import-timeout
error on save.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def set_import_config(redis_client: Any, temp_id: str, payload: dict, expire: int) -> None:
    try:
        redis_client.set(temp_id, json.dumps(payload, ensure_ascii=False), ex=expire)
    except Exception:
        logger.warning("Redis import-config set failed: key=%s", temp_id, exc_info=True)


def get_import_config(redis_client: Any, temp_id: str) -> str | None:
    try:
        return redis_client.get(temp_id)
    except Exception:
        logger.warning("Redis import-config get failed: key=%s", temp_id, exc_info=True)
        return None


__all__ = ["get_import_config", "set_import_config"]
