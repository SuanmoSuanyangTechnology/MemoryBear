"""Remote emotion reply policy provider for the split application service.

The reply policy is derived from the app's bound ontology (memory domain), so
the split service reaches it through the monolith internal callback channel
(``/api/internal/app-service/memory/emotion-policy``) instead of reading
ontology tables directly.

The enterprise wiring registers it under ``memory.emotion_reply_policy`` via
``enterprise_ext.app.register_app_plugins``. Open-source builds leave the
registry empty and the resolver keeps using its bundled JSON defaults.

Read-path semantics mirror the monolith provider: ``get_strategy`` never
raises and returns None whenever no strategy applies, so callers always fall
back to the base prompt.
"""

from __future__ import annotations

import logging
from typing import Any

from ..infrastructure.upstream import MonolithUpstream, UpstreamUnavailableError

logger = logging.getLogger(__name__)

EMOTION_POLICY_PLUGIN = "memory.emotion_reply_policy"

_STRATEGY_PATH = "/api/internal/app-service/memory/emotion-policy"


class RemoteEmotionPolicyProvider:
    """HTTP counterpart of the monolith ``EmotionReplyPolicyProvider``."""

    def __init__(self, upstream: MonolithUpstream) -> None:
        self._upstream = upstream

    async def get_strategy(
            self,
            app_id: Any,
            tenant_id: Any,
            emotion_code: str,
    ) -> dict | None:
        """Return this turn's strategy; every failure degrades to None."""
        if not app_id or not tenant_id or not emotion_code:
            return None
        try:
            response = await self._upstream.forward_async("POST", _STRATEGY_PATH, json_body={
                "app_id": str(app_id),
                "tenant_id": str(tenant_id),
                "emotion_code": emotion_code,
            })
        except UpstreamUnavailableError as exc:
            logger.warning(
                "[EmotionReply] policy callback unavailable, keeping base prompt: err=%s", exc
            )
            return None
        body = response.body if isinstance(response.body, dict) else {}
        if response.status_code != 200 or body.get("code") != 0:
            logger.warning(
                "[EmotionReply] policy callback rejected, keeping base prompt: status=%s code=%s",
                response.status_code, body.get("code"),
            )
            return None
        data = body.get("data")
        if not isinstance(data, dict) or not data.get("available"):
            logger.warning(
                "[EmotionReply] policy callback reports unavailable, keeping base prompt"
            )
            return None
        strategy = data.get("strategy")
        return strategy if isinstance(strategy, dict) else None


__all__ = ["EMOTION_POLICY_PLUGIN", "RemoteEmotionPolicyProvider"]
