"""Loop-safe asynchronous Neo4j driver lifecycle."""

from __future__ import annotations

import asyncio

from neo4j import AsyncDriver, AsyncGraphDatabase

from src.config import ProfileSettings


class Neo4jManager:
    """Lazily own one Neo4j driver for one process and event loop."""

    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._driver: AsyncDriver | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._driver is not None

    @property
    def database(self) -> str | None:
        return self._settings.neo4j_database

    async def driver(self) -> AsyncDriver:
        current_loop = asyncio.get_running_loop()
        if self._driver is not None and self._loop is not current_loop:
            raise RuntimeError("Neo4j driver cannot be shared across event loops")
        async with self._lock:
            if self._driver is None:
                self._driver = AsyncGraphDatabase.driver(
                    self._settings.neo4j_uri,
                    auth=(self._settings.neo4j_username, self._settings.neo4j_password),
                    max_connection_pool_size=self._settings.neo4j_max_pool_size,
                    connection_acquisition_timeout=(
                        self._settings.neo4j_acquisition_timeout_seconds
                    ),
                    max_connection_lifetime=self._settings.neo4j_max_connection_lifetime_seconds,
                    connection_timeout=self._settings.neo4j_connection_timeout_seconds,
                )
                self._loop = current_loop
            return self._driver

    async def ping(self) -> bool:
        await (await self.driver()).verify_connectivity()
        return True

    async def aclose(self) -> None:
        driver = self._driver
        self._driver = None
        self._loop = None
        if driver is not None:
            await driver.close()
