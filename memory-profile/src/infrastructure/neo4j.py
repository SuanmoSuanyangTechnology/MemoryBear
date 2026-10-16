"""Loop-safe asynchronous Neo4j driver lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from src.config import ProfileSettings

_DISABLED_NOTIFICATION_CLASSIFICATIONS = ["UNRECOGNIZED"]


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
                    notifications_disabled_classifications=(
                        _DISABLED_NOTIFICATION_CLASSIFICATIONS
                    ),
                )
                self._loop = current_loop
            return self._driver

    async def read_query(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """执行一条只读 Cypher，返回记录字典列表。

        这是本服务所有图读查询的统一出口：连接、会话、访问模式与事务形态都由本层
        决定，仓储层只负责提供 Cypher 与参数。

        为什么不用 ``driver.execute_query``：它会把每条查询包进一个托管事务
        （内部等价于 ``session.execute_read``，多出 BEGIN/COMMIT 往返）。实测同一条
        真实查询：``execute_query(..., routing_="r")`` 4.700 ms vs auto-commit
        ``session.run()`` 3.822 ms —— 每条查询省约 0.9 ms，而会话本身的开销只有
        0.012 ms。本服务的端点按设计会扇出十几条小查询（graph_data 一轮约 20 条），
        这个系数会被直接放大。

        ``default_access_mode="READ"`` 与原先 ``execute_query(routing_="r")`` 的读
        路由等价，集群部署下同样把查询打到读副本。每条查询独占一个 session，即独占
        一个池连接，因此**并发能力不变**（同一 session 不允许并发执行查询）。

        与 ``execute_query`` 的差异：托管事务自带的瞬时故障自动重试没有了。本服务的
        查询都是简单只读（读事务不产生死锁），只有集群切主等少数场景会出现瞬时错误，
        这类错误会直接向上抛出。
        """
        driver = await self.driver()
        async with driver.session(
            database=self.database, default_access_mode="READ"
        ) as session:
            result = await session.run(query, **params)
            return await result.data()

    async def ping(self) -> bool:
        await (await self.driver()).verify_connectivity()
        return True

    async def aclose(self) -> None:
        driver = self._driver
        self._driver = None
        self._loop = None
        if driver is not None:
            await driver.close()
