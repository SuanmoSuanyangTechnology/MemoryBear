"""Best-effort query-plan caching on the service-owned Redis client."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .models import GraphIndexRuntime, GraphQueryPlan
from .prompts import QUERY_PLAN_PROMPT_VERSION

logger = logging.getLogger(__name__)

_CACHE_SCHEMA_VERSION = "1"
_DEFAULT_TTL_SECONDS = 5 * 60


class GraphQueryPlanCache:
    def __init__(
        self,
        client_factory: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._client_factory = client_factory
        self._ttl_seconds = max(1, int(ttl_seconds))

    @staticmethod
    def build_key(runtime: GraphIndexRuntime, query: str) -> str:
        normalized_query = str(query).strip()
        query_hash = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
        payload = {
            "workspace_id": runtime.workspace_id,
            "provider": str(runtime.llm.provider),
            "model_name": runtime.llm.model_name,
            "base_url": runtime.llm.base_url or "",
            "prompt_version": QUERY_PLAN_PROMPT_VERSION,
            "schema_version": _CACHE_SCHEMA_VERSION,
            "query_hash": query_hash,
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"evidence_graph:query_plan:{_CACHE_SCHEMA_VERSION}:{digest}"

    async def get(self, key: str) -> GraphQueryPlan | None:
        try:
            client = await self._client_factory()
            raw_value = await client.get(key)
        except Exception as exc:
            logger.info(
                "[EvidenceGraph] query_plan_cache status=error operation=get error_type=%s",
                type(exc).__name__,
            )
            return None
        if raw_value is None:
            logger.info("[EvidenceGraph] query_plan_cache status=miss")
            return None
        try:
            plan = GraphQueryPlan.model_validate_json(raw_value)
        except Exception as exc:
            logger.info(
                "[EvidenceGraph] query_plan_cache status=error operation=decode error_type=%s",
                type(exc).__name__,
            )
            return None
        logger.info("[EvidenceGraph] query_plan_cache status=hit")
        return plan

    async def set(self, key: str, plan: GraphQueryPlan) -> bool:
        try:
            client = await self._client_factory()
            await client.set(key, plan.model_dump_json(), ex=self._ttl_seconds)
        except Exception as exc:
            logger.info(
                "[EvidenceGraph] query_plan_cache status=error operation=set error_type=%s",
                type(exc).__name__,
            )
            return False
        logger.info("[EvidenceGraph] query_plan_cache status=stored")
        return True


__all__ = ["GraphQueryPlanCache"]
