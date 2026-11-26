"""Conversation activity markers the sliding-window write path depends on.

Two keys decide whether a conversation's pending messages get dispatched:

* ``conv_active:<conversation_id>`` — a short TTL marker the idle sweeper uses
  to tell "the user is still typing" from "this conversation went quiet";
* ``pending_conversations`` — the set of conversations an ingest path has armed.

Both are owned by the API today and read by the API's sweeper, so this service
writes the same key names with the same TTLs. Failure is never fatal: a missing
marker only means the flush sweeper may pick the conversation up sooner.
"""

from __future__ import annotations

import logging

from redis import Redis

logger = logging.getLogger(__name__)

CONV_ACTIVE_KEY_PREFIX = "conv_active:"
CONV_ACTIVE_TTL_SECONDS = 300
PENDING_CONVERSATIONS_SET_KEY = "pending_conversations"


class ConversationActivity:
    """Refresh the activity marker and the pending set for one conversation."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def refresh_active(self, conversation_id: str) -> None:
        """Extend the conversation's activity window by one TTL."""

        try:
            await self._client.set(
                f"{CONV_ACTIVE_KEY_PREFIX}{conversation_id}",
                "1",
                ex=CONV_ACTIVE_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - a marker miss is not a write failure
            logger.warning(
                "conversation activity marker failed: conversation=%s error=%s",
                conversation_id,
                exc,
            )

    async def mark_pending(self, conversation_id: str) -> None:
        """Arm the conversation for the flush sweeper."""

        try:
            await self._client.sadd(PENDING_CONVERSATIONS_SET_KEY, conversation_id)
        except Exception as exc:  # noqa: BLE001 - the sweeper's own scan still covers it
            logger.debug(
                "conversation pending marker failed: conversation=%s error=%s",
                conversation_id,
                exc,
            )


__all__ = [
    "CONV_ACTIVE_KEY_PREFIX",
    "CONV_ACTIVE_TTL_SECONDS",
    "ConversationActivity",
    "PENDING_CONVERSATIONS_SET_KEY",
]
