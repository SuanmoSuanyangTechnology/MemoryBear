"""Loop-safe asynchronous Elasticsearch lifecycle."""

from __future__ import annotations

import asyncio
import logging

from elasticsearch import AsyncElasticsearch

from src.config import ProfileSettings

logger = logging.getLogger(__name__)


class ElasticsearchManager:
    """Lazily own one Elasticsearch client for one process and event loop."""

    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._client: AsyncElasticsearch | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._client is not None

    async def client(self) -> AsyncElasticsearch:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is not current_loop:
            raise RuntimeError("Elasticsearch client cannot be shared across event loops")
        async with self._lock:
            if self._client is None:
                options: dict[str, object] = {
                    "hosts": self._settings.elasticsearch_hosts,
                    "request_timeout": self._settings.elasticsearch_request_timeout_seconds,
                    "retry_on_timeout": self._settings.elasticsearch_retry_on_timeout,
                    "max_retries": self._settings.elasticsearch_max_retries,
                    "connections_per_node": self._settings.elasticsearch_connections_per_node,
                    "verify_certs": self._settings.elasticsearch_verify_certs,
                }
                if self._settings.elasticsearch_username:
                    options["basic_auth"] = (
                        self._settings.elasticsearch_username,
                        self._settings.elasticsearch_password,
                    )
                if self._settings.elasticsearch_ca_certs:
                    options["ca_certs"] = self._settings.elasticsearch_ca_certs
                self._client = AsyncElasticsearch(**options)
                self._loop = current_loop
            return self._client

    def pool_snapshot(self) -> dict[str, object] | None:
        """Return transport node counts without issuing a request."""

        client = self._client
        if client is None:
            return None
        try:
            transport = getattr(client, "transport", None) or getattr(client, "_transport", None)
            node_pool = getattr(transport, "node_pool", None)
            if node_pool is None:
                return None
            return {"nodes": len(node_pool), "alive": len(node_pool.all())}
        except Exception as exc:
            logger.warning(
                "Failed to read Elasticsearch pool state: %s",
                type(exc).__name__,
                extra={"event": "elasticsearch_pool_snapshot_failed"},
            )
            return None

    async def ping(self) -> bool:
        return bool(await (await self.client()).ping())

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._loop = None
        if client is not None:
            await client.close()
