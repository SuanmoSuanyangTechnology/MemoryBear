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
from time import perf_counter
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
        started = perf_counter()
        if not app_id or not tenant_id or not emotion_code:
            logger.debug(
                "[EmotionReply] skipped event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=missing_identity", tenant_id, app_id,
            )
            return None
        try:
            response = await self._upstream.forward_async("POST", _STRATEGY_PATH, json_body={
                "app_id": str(app_id),
                "tenant_id": str(tenant_id),
                "emotion_code": emotion_code,
            })
        except UpstreamUnavailableError as exc:
            logger.warning(
                "[EmotionReply] failed event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=upstream_unavailable error_type=%s elapsed_ms=%.1f",
                tenant_id, app_id, type(exc).__name__, (perf_counter() - started) * 1000,
            )
            return None
        body = response.body if isinstance(response.body, dict) else {}
        if response.status_code != 200 or body.get("code") != 0:
            logger.warning(
                "[EmotionReply] failed event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=upstream_rejected status_code=%s elapsed_ms=%.1f",
                tenant_id, app_id, response.status_code, (perf_counter() - started) * 1000,
            )
            return None
        data = body.get("data")
        if not isinstance(data, dict) or not data.get("available"):
            logger.warning(
                "[EmotionReply] skipped event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=provider_unavailable elapsed_ms=%.1f",
                tenant_id, app_id, (perf_counter() - started) * 1000,
            )
            return None
        strategy = data.get("strategy")
        source = strategy.get("ontology_source") if isinstance(strategy, dict) else None
        logger.info(
            "[EmotionReply] %s event=policy_callback tenant_id=%s app_id=%s "
            "ontology_id=%s ontology_source=%s result=%s reason=%s elapsed_ms=%.1f",
            "completed" if isinstance(strategy, dict) else "skipped", tenant_id, app_id,
            strategy.get("ontology_id") if isinstance(strategy, dict) else None,
            source if source in ("custom", "system_default") else "none",
            "hit" if isinstance(strategy, dict) else "miss",
            "emotion_configured" if isinstance(strategy, dict) else "strategy_unavailable",
            (perf_counter() - started) * 1000,
        )
        return strategy if isinstance(strategy, dict) else None


__all__ = ["EMOTION_POLICY_PLUGIN", "RemoteEmotionPolicyProvider"]
