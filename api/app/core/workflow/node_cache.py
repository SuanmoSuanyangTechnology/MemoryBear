import datetime
import hashlib
import json
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel

from app.core.utils.datetime_utils import utcnow_naive
from app.db import get_db_context
from app.repositories.workflow_repository import WorkflowNodeCacheRepository


DEFAULT_CACHEABLE_NODE_TYPES = {
    "llm",
    "code",
    "knowledge-retrieval",
    "jinja-render",
    "parameter-extractor",
    "question-classifier",
    "var-aggregator",
}


def normalize_cache_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return normalize_cache_value(value.model_dump())
    if isinstance(value, dict):
        return {str(k): normalize_cache_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize_cache_value(item) for item in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return normalize_cache_value(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return normalize_cache_value(value.dict())
    return str(value)


class WorkflowNodeCacheManager:
    def __init__(
            self,
            *,
            app_id: str | uuid.UUID | None,
            workflow_config_id: str | uuid.UUID | None,
            node_id: str,
            node_type: str,
            node_name: str | None,
    ):
        self.app_id = self._parse_uuid(app_id)
        self.workflow_config_id = self._parse_uuid(workflow_config_id)
        self.node_id = node_id
        self.node_type = node_type
        self.node_name = node_name

    @staticmethod
    def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
        if not value:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError):
            return None

    def is_available(self) -> bool:
        return self.app_id is not None

    def build_cache_key(self, input_data: Any) -> str:
        payload = {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "input_data": normalize_cache_value(input_data),
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def serialize(cache) -> dict[str, Any]:
        return {
            "id": cache.id,
            "app_id": cache.app_id,
            "workflow_config_id": cache.workflow_config_id,
            "node_id": cache.node_id,
            "node_type": cache.node_type,
            "node_name": cache.node_name,
            "cache_key": cache.cache_key,
            "source": cache.source,
            "status": cache.status,
            "input_data": cache.input_data,
            "result_data": cache.result_data,
            "hit_count": cache.hit_count,
            "last_hit_at": cache.last_hit_at,
            "expires_at": cache.expires_at,
            "invalidated_at": cache.invalidated_at,
            "meta_data": cache.meta_data or {},
            "created_at": cache.created_at,
            "updated_at": cache.updated_at,
        }

    def get_active_cache(self, cache_key: str) -> dict[str, Any] | None:
        if not self.app_id:
            return None

        now = utcnow_naive()
        with get_db_context() as db:
            repo = WorkflowNodeCacheRepository(db)
            cache = repo.get_active_by_key(self.app_id, self.node_id, cache_key)
            if not cache:
                return None

            if cache.expires_at and cache.expires_at <= now:
                cache.status = "expired"
                cache.invalidated_at = now
                db.commit()
                return None

            cache.hit_count = int(cache.hit_count or 0) + 1
            cache.last_hit_at = now
            db.commit()
            db.refresh(cache)
            return self.serialize(cache)

    def get_latest_cache(self, include_inactive: bool = False) -> dict[str, Any] | None:
        if not self.app_id:
            return None

        now = utcnow_naive()
        with get_db_context() as db:
            repo = WorkflowNodeCacheRepository(db)
            repo.invalidate_expired(now)
            db.commit()
            cache = repo.get_latest_by_node(self.app_id, self.node_id, include_inactive=include_inactive)
            if not cache:
                return None
            return self.serialize(cache)

    def save_cache(
            self,
            *,
            cache_key: str,
            input_data: Any,
            result_data: dict[str, Any],
            source: str,
            ttl_seconds: int | None,
            meta_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.app_id:
            return None

        now = utcnow_naive()
        expires_at = now + datetime.timedelta(seconds=ttl_seconds) if ttl_seconds else None
        normalized_input = normalize_cache_value(input_data)
        normalized_result = normalize_cache_value(result_data)
        normalized_meta = normalize_cache_value(meta_data or {})

        with get_db_context() as db:
            repo = WorkflowNodeCacheRepository(db)
            cache = repo.get_active_by_key(self.app_id, self.node_id, cache_key)
            if cache and cache.expires_at and cache.expires_at <= now:
                cache.status = "expired"
                cache.invalidated_at = now
                cache = None

            if cache is None:
                cache = repo.create(
                    app_id=self.app_id,
                    workflow_config_id=self.workflow_config_id,
                    node_id=self.node_id,
                    node_type=self.node_type,
                    node_name=self.node_name,
                    cache_key=cache_key,
                    source=source,
                    status="active",
                    input_data=normalized_input,
                    result_data=normalized_result,
                    hit_count=0,
                    last_hit_at=None,
                    expires_at=expires_at,
                    invalidated_at=None,
                    meta_data=normalized_meta,
                )
            else:
                cache.workflow_config_id = self.workflow_config_id
                cache.node_type = self.node_type
                cache.node_name = self.node_name
                cache.source = source
                cache.status = "active"
                cache.input_data = normalized_input
                cache.result_data = normalized_result
                cache.expires_at = expires_at
                cache.invalidated_at = None
                cache.meta_data = normalized_meta

            db.commit()
            db.refresh(cache)
            return self.serialize(cache)

    def update_latest_cache(
            self,
            *,
            result_data: dict[str, Any],
            meta_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.app_id:
            return None

        with get_db_context() as db:
            repo = WorkflowNodeCacheRepository(db)
            cache = repo.get_latest_by_node(self.app_id, self.node_id, include_inactive=False)
            if not cache:
                return None
            cache.result_data = normalize_cache_value(result_data)
            if meta_data is not None:
                cache.meta_data = normalize_cache_value(meta_data)
            db.commit()
            db.refresh(cache)
            return self.serialize(cache)

    def invalidate_latest_cache(self) -> int:
        if not self.app_id:
            return 0

        now = utcnow_naive()
        with get_db_context() as db:
            repo = WorkflowNodeCacheRepository(db)
            affected = repo.invalidate_by_node(self.app_id, self.node_id, invalidated_at=now)
            db.commit()
            return affected
