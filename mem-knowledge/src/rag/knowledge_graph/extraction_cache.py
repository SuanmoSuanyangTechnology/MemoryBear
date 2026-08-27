"""Best-effort Redis cache for Evidence Graph extraction results."""

import hashlib
import json
import logging
from typing import Any

from .models import ExtractionBatch, ExtractionResult, GraphIndexRuntime
from .prompts import EXTRACTION_PROMPT_VERSION

logger = logging.getLogger(__name__)

_CACHE_SCHEMA_VERSION = "1"
_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60


class GraphExtractionCache:
    def __init__(self, redis: Any, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl_seconds = max(1, int(ttl_seconds))

    @staticmethod
    def build_key(runtime: GraphIndexRuntime, batch: ExtractionBatch) -> str:
        payload = {
            "workspace_id": runtime.workspace_id,
            "provider": str(runtime.llm.provider),
            "model_name": runtime.llm.model_name,
            "api_base": runtime.llm.base_url or "",
            "prompt_version": EXTRACTION_PROMPT_VERSION,
            "schema_version": _CACHE_SCHEMA_VERSION,
            "scene_name": " ".join(runtime.scene_name.split()),
            "entity_types": list(runtime.entity_types),
            "content_hash": hashlib.sha256(batch.text.encode("utf-8")).hexdigest(),
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"evidence_graph:extraction:{_CACHE_SCHEMA_VERSION}:{digest}"

    async def get(self, key: str) -> ExtractionResult | None:
        try:
            raw_value = await self._redis.get(key)
        except Exception as exc:
            logger.info(
                "[EvidenceGraph] extraction_cache status=error operation=get error_type=%s",
                type(exc).__name__,
            )
            return None
        if raw_value is None:
            return None
        try:
            return ExtractionResult.model_validate_json(raw_value)
        except Exception as exc:
            logger.info(
                "[EvidenceGraph] extraction_cache status=error operation=decode error_type=%s",
                type(exc).__name__,
            )
            return None

    async def set(self, key: str, result: ExtractionResult) -> bool:
        try:
            await self._redis.set(
                key,
                result.model_dump_json(),
                ex=self._ttl_seconds,
            )
        except Exception as exc:
            logger.info(
                "[EvidenceGraph] extraction_cache status=error operation=set error_type=%s",
                type(exc).__name__,
            )
            return False
        return True


__all__ = ["GraphExtractionCache"]
