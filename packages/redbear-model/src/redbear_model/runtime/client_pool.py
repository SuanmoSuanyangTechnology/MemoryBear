"""Explicitly managed HTTP clients shared by provider adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from redbear_model.contracts import ModelRuntimeOptions


@dataclass(frozen=True)
class HttpClients:
    sync: object
    async_client: object
    timeout: httpx.Timeout | None = None


class ModelClientPool:
    """Own sync and async HTTP clients for one runtime lifecycle."""

    def __init__(self, options: ModelRuntimeOptions):
        self._options = options
        self._clients: HttpClients | None = None
        self._closed = False

    def get_http_clients(self) -> HttpClients:
        if self._closed:
            raise RuntimeError("ModelClientPool is closed")
        if self._clients is None:
            timeout = httpx.Timeout(
                timeout=self._options.timeout_s,
                connect=60.0,
                read=self._options.timeout_s,
                write=60.0,
                pool=10.0,
            )
            limits = httpx.Limits(
                max_connections=self._options.http_max_connections,
                max_keepalive_connections=(
                    self._options.http_max_keepalive_connections
                ),
            )
            self._clients = HttpClients(
                sync=httpx.Client(
                    timeout=timeout,
                    limits=limits,
                    follow_redirects=True,
                    trust_env=self._options.http_trust_env,
                ),
                async_client=httpx.AsyncClient(
                    timeout=timeout,
                    limits=limits,
                    follow_redirects=True,
                    trust_env=self._options.http_trust_env,
                ),
                timeout=timeout,
            )
        return self._clients

    def close(self) -> None:
        if self._closed:
            return
        if self._clients is None:
            self._closed = True
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "ModelClientPool.close() cannot run inside an event loop; "
                "use 'await aclose()'"
            )
        close = getattr(self._clients.sync, "close", None)
        if callable(close):
            close()
        aclose = getattr(self._clients.async_client, "aclose", None)
        if callable(aclose):
            asyncio.run(aclose())
        self._closed = True

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._clients is None:
            self._closed = True
            return
        close = getattr(self._clients.sync, "close", None)
        if callable(close):
            close()
        aclose = getattr(self._clients.async_client, "aclose", None)
        if callable(aclose):
            await aclose()
        self._closed = True
