"""Crash recovery for a model switch interrupted between its two halves.

The switch spans two services: identity commits the workspace columns, then
registers the rebuild job in memory-profile. A process crash in that gap
leaves the workspace on a new embedding model with no job — the vector spaces
mix and nothing retries on its own. A Redis marker written *before* the
column commit closes the gap:

* the startup sweep replays every surviving marker once — a crash implies a
  restart, so boot covers the switches that killed the previous process;
* the lazy replay runs from the next ``PUT /workspace_models`` for the same
  workspace, before it writes anything, so a switch stranded by an eviction or
  an already-passed sweep still heals on the next edit.

Replay decides from the workspace row, not from the error text: if the current
embedding no longer equals the marker's target, the column write never
committed (or a later switch superseded it), so there is nothing to heal and
the marker is dropped. Otherwise the registration is retried with the stored
``Idempotency-Key`` — a call whose response was lost replays its first answer
instead of registering a second job. A 2xx or a gone workspace (404) clears the
marker; every other answer keeps it until the TTL, so an unreachable
memory-profile never blocks model switches forever.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import select

from src.config import settings
from src.core.exceptions import BusinessException
from src.db import get_db_context
from src.integrations import memory_profile
from src.models.workspace_model import Workspace
from src.redis import get_thread_safe_sync_redis

logger = logging.getLogger(__name__)

#: A switch stranded longer than this stops protecting anything: the next
#: successful registration supersedes it (one active job per workspace anyway).
MARKER_TTL_SECONDS = 3600
_MARKER_PREFIX = f"workspace_model_switch:{settings.DB_NAME}:"

#: Marker fields replay cannot work without.
_REQUIRED_FIELDS = (
    "workspace_id",
    "tenant_id",
    "user_id",
    "new_embedding_config_id",
    "idempotency_key",
)


def _marker_key(workspace_id: uuid.UUID | str) -> str:
    return f"{_MARKER_PREFIX}{workspace_id}"


def mark_switch(
    *,
    workspace_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    old_embedding_config_id: str | None,
    new_embedding_config_id: str,
) -> dict[str, Any] | None:
    """Record the pending switch; returns the marker (with its idempotency key).

    Best-effort: a Redis outage must not block model switches. Without the
    marker a crash still surfaces as the ``reembed_job_id`` staying null, and
    the next same-model write reaches the endpoint's no-op path.
    """

    marker = {
        "workspace_id": str(workspace_id),
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "old_embedding_config_id": old_embedding_config_id,
        "new_embedding_config_id": new_embedding_config_id,
        "idempotency_key": uuid.uuid4().hex,
        "created_at": int(time.time()),
    }
    try:
        get_thread_safe_sync_redis().set(
            _marker_key(workspace_id), json.dumps(marker), ex=MARKER_TTL_SECONDS
        )
    except RedisError:
        logger.warning(
            "model switch marker write failed workspace_id=%s", workspace_id, exc_info=True
        )
        return None
    return marker


def clear_switch(workspace_id: uuid.UUID) -> None:
    try:
        get_thread_safe_sync_redis().delete(_marker_key(workspace_id))
    except RedisError:
        # A stale marker is harmless: its next replay sees the workspace row
        # already at (or past) the target and drops it.
        logger.warning(
            "model switch marker delete failed workspace_id=%s", workspace_id, exc_info=True
        )


def load_switch(workspace_id: uuid.UUID) -> dict[str, Any] | None:
    raw = _read(_marker_key(workspace_id))
    workspace = _parse(raw)
    if raw is not None and workspace is None:
        clear_switch(workspace_id)
    return workspace


def _read(key: str) -> str | None:
    try:
        return get_thread_safe_sync_redis().get(key)
    except RedisError:
        logger.warning("model switch marker read failed key=%s", key, exc_info=True)
        return None


def _parse(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        marker = json.loads(raw)
    except ValueError:
        logger.warning("model switch marker is not valid JSON")
        return None
    if not isinstance(marker, dict) or any(
        not marker.get(field) for field in _REQUIRED_FIELDS
    ):
        logger.warning("model switch marker is missing required fields")
        return None
    return marker


def _read_current_embedding(workspace_id: uuid.UUID) -> str | None | bool:
    """The workspace's committed embedding, or ``False`` when it is gone."""

    with get_db_context() as session:
        row = session.execute(
            select(Workspace.embedding).where(Workspace.id == workspace_id)
        ).first()
    if row is None:
        return False
    value = row[0]
    return None if value is None else str(value)


def _replay(marker: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    """Heal one marker; returns the pending failure to forward, or None.

    Raises ``BusinessException(SERVICE_UNAVAILABLE)`` when memory-profile is
    unreachable — the caller keeps the marker and reports the outage.
    """

    try:
        workspace_id = uuid.UUID(marker["workspace_id"])
        user_id = uuid.UUID(marker["user_id"])
        tenant_id = uuid.UUID(marker["tenant_id"])
    except ValueError:
        logger.warning("model switch marker carries malformed ids")
        try:
            get_thread_safe_sync_redis().delete(_marker_key(marker["workspace_id"]))
        except RedisError:
            logger.warning("malformed model switch marker delete failed", exc_info=True)
        return None

    current = _read_current_embedding(workspace_id)
    if current is False:
        clear_switch(workspace_id)
        return None
    if current != marker["new_embedding_config_id"]:
        # The column write never committed, or a later switch superseded it:
        # no rebuild is owed for this marker.
        clear_switch(workspace_id)
        return None

    status, body = memory_profile.register_reembed_job(
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        old_embedding_config_id=marker.get("old_embedding_config_id"),
        new_embedding_config_id=marker["new_embedding_config_id"],
        idempotency_key=marker["idempotency_key"],
    )
    if 200 <= status < 300 or status == 404:
        clear_switch(workspace_id)
        logger.info(
            "stranded model switch healed workspace_id=%s status=%s",
            workspace_id,
            status,
        )
        return None
    logger.warning(
        "model switch replay still failing workspace_id=%s status=%s", workspace_id, status
    )
    return status, body


def replay_switch_for_workspace(
    workspace_id: uuid.UUID,
) -> tuple[int, dict[str, Any]] | None:
    """Lazy replay before a new switch writes the same workspace.

    Returns ``None`` when there is nothing pending (or it just healed); a
    non-None answer is the memory-profile failure the caller must forward
    without writing anything — the marker's pre-image is still the row.
    """

    marker = load_switch(workspace_id)
    if marker is None:
        return None
    return _replay(marker)


def replay_pending_switches() -> None:
    """Startup sweep: replay every marker once (called in a worker thread)."""

    try:
        keys = list(
            get_thread_safe_sync_redis().scan_iter(match=f"{_MARKER_PREFIX}*", count=100)
        )
    except RedisError:
        logger.warning("model switch sweep could not scan markers", exc_info=True)
        return
    healed = 0
    for key in keys:
        marker = _parse(_read(key))
        if marker is None:
            continue
        try:
            if _replay(marker) is None:
                healed += 1
        except BusinessException:
            logger.warning("model switch sweep replay deferred key=%s", key)
        except Exception:
            logger.warning("model switch sweep replay failed key=%s", key, exc_info=True)
    logger.info(
        "model switch sweep finished: markers=%s healed=%s", len(keys), healed
    )


__all__ = [
    "MARKER_TTL_SECONDS",
    "clear_switch",
    "load_switch",
    "mark_switch",
    "replay_pending_switches",
    "replay_switch_for_workspace",
]
