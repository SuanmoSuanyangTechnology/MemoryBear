"""Loop-safe asynchronous Elasticsearch lifecycle."""

from __future__ import annotations

import asyncio

from elasticsearch import AsyncElasticsearch

from ..config import KnowledgeSettings


class ElasticsearchManager:
    """Lazily own one Elasticsearch client for one event loop."""

    def __init__(self, settings: KnowledgeSettings):
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
                    "basic_auth": (
                        self._settings.elasticsearch_username,
                        self._settings.elasticsearch_password.get_secret_value(),
                    ),
                    "request_timeout": self._settings.elasticsearch_request_timeout,
                    "retry_on_timeout": self._settings.elasticsearch_retry_on_timeout,
                    "max_retries": self._settings.elasticsearch_max_retries,
                    "connections_per_node": (
                        self._settings.kb_es_connections_per_node
                    ),
                    "verify_certs": self._settings.elasticsearch_verify_certs,
                }
                if self._settings.elasticsearch_ca_certs:
                    options["ca_certs"] = self._settings.elasticsearch_ca_certs
                self._client = AsyncElasticsearch(**options)
                self._loop = current_loop
            return self._client

    async def ping(self) -> bool:
        return bool(await (await self.client()).ping())

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._loop = None
        if client is not None:
            await client.close()

    def reset_after_fork(self) -> None:
        self._client = None
        self._loop = None
        self._lock = asyncio.Lock()
