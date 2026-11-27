"""Remote emotion reply policy provider for the split application service.

The reply policy is derived from the app's bound ontology, which belongs to
the memory domain; this service reaches it over memory-enterprise's
app-emotion-policy internal route instead of reading ontology tables directly.

The enterprise wiring registers it under ``memory.emotion_reply_policy`` via
``enterprise_ext.app.register_app_plugins``. Open-source builds leave the
registry empty and the resolver keeps using its bundled JSON defaults, and in
community deployments the memory-enterprise channel is unconfigured, so every
lookup degrades to the bundled JSON as well.

Read-path semantics: ``get_strategy`` never raises and returns None whenever
no strategy applies, so callers always fall back to the base prompt.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from ..integrations.memory_enterprise import (
    MemoryEnterpriseClient,
    MemoryEnterpriseProtocolError,
    MemoryEnterpriseUnavailableError,
)

logger = logging.getLogger(__name__)

EMOTION_POLICY_PLUGIN = "memory.emotion_reply_policy"


class RemoteEmotionPolicyProvider:
    """Memory-enterprise counterpart of the monolith reply-policy provider."""

    def __init__(self, client: MemoryEnterpriseClient) -> None:
        self._client = client

    async def get_strategy(
            self,
            app_id: Any,
            tenant_id: Any,
            emotion_code: str,
            *,
            workspace_id: Any = None,
    ) -> dict | None:
        """Return this turn's strategy; every failure degrades to None."""
        started = perf_counter()
        if not app_id or not tenant_id or not workspace_id or not emotion_code:
            logger.debug(
                "[EmotionReply] skipped event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=missing_identity", tenant_id, app_id,
            )
            return None
        try:
            envelope = await self._client.app_emotion_policy(
                app_id=app_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                emotion_code=emotion_code,
            )
        except MemoryEnterpriseUnavailableError as exc:
            logger.warning(
                "[EmotionReply] failed event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=provider_unavailable error_type=%s elapsed_ms=%.1f",
                tenant_id, app_id, type(exc).__name__, (perf_counter() - started) * 1000,
            )
            return None
        except MemoryEnterpriseProtocolError as exc:
            logger.warning(
                "[EmotionReply] failed event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=provider_protocol_error error_type=%s elapsed_ms=%.1f",
                tenant_id, app_id, type(exc).__name__, (perf_counter() - started) * 1000,
            )
            return None
        if envelope.get("code") != 0:
            logger.warning(
                "[EmotionReply] failed event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=provider_rejected code=%s elapsed_ms=%.1f",
                tenant_id, app_id, envelope.get("code"),
                (perf_counter() - started) * 1000,
            )
            return None
        strategy = envelope.get("data")
        if not isinstance(strategy, dict):
            logger.info(
                "[EmotionReply] skipped event=policy_callback tenant_id=%s app_id=%s "
                "result=miss reason=strategy_unavailable elapsed_ms=%.1f",
                tenant_id, app_id, (perf_counter() - started) * 1000,
            )
            return None
        source = strategy.get("ontology_source")
        logger.info(
            "[EmotionReply] completed event=policy_callback tenant_id=%s app_id=%s "
            "ontology_id=%s ontology_source=%s result=hit reason=emotion_configured "
            "elapsed_ms=%.1f",
            tenant_id, app_id, strategy.get("ontology_id"),
            source if source in ("custom", "system_default") else "none",
            (perf_counter() - started) * 1000,
        )
        return strategy


__all__ = ["EMOTION_POLICY_PLUGIN", "RemoteEmotionPolicyProvider"]
