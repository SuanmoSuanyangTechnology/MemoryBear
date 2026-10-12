"""Process-level orchestration for profile infrastructure managers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from src.config import ProfileSettings
from src.infrastructure.elasticsearch import ElasticsearchManager
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.infrastructure.redis import RedisManager

HealthCheck = Callable[[], Awaitable[bool]]


class ProfileRuntime:
    """Compose lazy process resources without owning client construction details."""

    def __init__(self, settings: ProfileSettings) -> None:
        self.settings = settings
        self.postgres = PostgresManager(settings)
        self.neo4j = Neo4jManager(settings)
        self.redis = RedisManager(settings)
        self.elasticsearch = ElasticsearchManager(settings)

    async def readiness(self) -> dict[str, str]:
        timeout = self.settings.health_probe_timeout_seconds
        checks: dict[str, HealthCheck] = {
            "postgres": self.postgres.ping,
            "neo4j": self.neo4j.ping,
            "redis": self.redis.ping,
            "elasticsearch": self.elasticsearch.ping,
        }

        async def probe(name: str, check: HealthCheck) -> tuple[str, str]:
            try:
                healthy = await asyncio.wait_for(check(), timeout=timeout)
                return name, "up" if healthy else "down"
            except Exception:
                return name, "down"

        return dict(await asyncio.gather(*(probe(name, check) for name, check in checks.items())))

    async def aclose(self) -> None:
        await asyncio.gather(
            self.postgres.aclose(),
            self.neo4j.aclose(),
            self.redis.aclose(),
            self.elasticsearch.aclose(),
        )
