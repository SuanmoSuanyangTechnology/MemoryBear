"""Write-capable Neo4j session for graph mutations.

The storage ``Neo4jClient`` is intentionally read-only and its contract test
forbids adding write methods. Graph mutations therefore borrow the process
driver through ``Neo4jManager`` and open their own managed write transactions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class Neo4jGraphWriter:
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    async def execute_write_transaction(
        self, operation: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        driver = await self._manager.driver()
        database = getattr(self._manager, "database", None)
        kwargs = {"database": database} if database else {}
        async with driver.session(**kwargs) as session:
            return await session.execute_write(operation)


__all__ = ["Neo4jGraphWriter"]