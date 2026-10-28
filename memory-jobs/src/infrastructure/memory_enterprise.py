"""Blocking client for the optional memory-enterprise service.

The open build has no enterprise tables. An enterprise deployment runs the
closed-source ``memory-enterprise`` service, which owns those tables and their
migrations, and points this worker at it — so the worker never reads a schema it
does not own.

Every call here is best-effort: an unreachable or misconfigured service only
costs the enterprise-derived fields (currently scene names), never the task.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import httpx

from src.config import JobsSettings

logger = logging.getLogger(__name__)

#: Scene-name resolution endpoint on the enterprise service.
SCENE_NAMES_PATH = "/internal/v1/ontology/scene-names"


class MemoryEnterpriseManager:
    """Lazily-created, thread-safe HTTP client for the enterprise service."""

    def __init__(
        self,
        settings: JobsSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Whether an enterprise service is configured at all."""

        return bool(self.settings.memory_enterprise_base_url)

    @property
    def initialized(self) -> bool:
        return self._client is not None

    def client(self) -> httpx.Client:
        client = self._client
        if client is not None:
            return client
        with self._lock:
            if self._client is None:
                headers = {}
                if self.settings.memory_enterprise_token:
                    headers["X-Internal-Token"] = self.settings.memory_enterprise_token
                self._client = httpx.Client(
                    base_url=self.settings.memory_enterprise_base_url.rstrip("/"),
                    timeout=self.settings.memory_enterprise_timeout_seconds,
                    headers=headers,
                    transport=self._transport,
                )
            return self._client

    def scene_names(self, scene_ids: Sequence[UUID]) -> Mapping[str, str]:
        """Resolve ``scene_type_id`` → scene name; empty when unavailable."""

        if not self.enabled or not scene_ids:
            return {}
        try:
            response = self.client().post(
                SCENE_NAMES_PATH,
                json={"scene_ids": [str(scene_id) for scene_id in scene_ids]},
            )
            response.raise_for_status()
            payload: Any = response.json()
        except Exception as exc:
            logger.warning(
                "memory-enterprise scene names unavailable error=%s",
                type(exc).__name__,
                exc_info=True,
                extra={
                    "event": "enterprise_scene_names_failed",
                    "exception_type": type(exc).__name__,
                },
            )
            return {}
        names = payload.get("names") if isinstance(payload, dict) else None
        if not isinstance(names, dict):
            logger.warning(
                "memory-enterprise returned an unexpected payload",
                extra={"event": "enterprise_scene_names_bad_payload"},
            )
            return {}
        return {
            key: value
            for key, value in names.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def reset_after_fork(self) -> None:
        """A forked child must not reuse the parent's connection pool."""

        client, self._client = self._client, None
        if client is not None:
            client.close()

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()
