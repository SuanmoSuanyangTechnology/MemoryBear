import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from app.core.config import settings


logger = logging.getLogger(__name__)

DependencyCheck = Callable[[], Awaitable[bool]]


async def _check_postgresql() -> bool:
    from sqlalchemy import text

    from app.db import async_engine

    async with async_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def _check_redis() -> bool:
    from app.aioRedis import aio_redis

    return bool(await aio_redis.ping())


async def _check_neo4j() -> bool:
    from app.repositories.neo4j.neo4j_connector import Neo4jConnector

    connector = Neo4jConnector(shared_driver=True)
    await connector.execute_query("RETURN 1 AS ping")
    return True


async def _check_elasticsearch() -> bool:
    from app.core.rag.retrieval.async_elasticsearch import (
        AsyncElasticsearchClientProvider,
    )

    client = await AsyncElasticsearchClientProvider.get_shared_client()
    return bool(await client.ping())


def _broker_is_available() -> bool:
    from app.celery_app import celery_app

    with celery_app.connection_for_read(connect_timeout=1) as connection:
        connection.connect()
        return bool(connection.connected)


async def _check_broker() -> bool:
    return await asyncio.to_thread(_broker_is_available)


def _default_checks() -> dict[str, DependencyCheck]:
    return {
        "postgresql": _check_postgresql,
        "redis": _check_redis,
        "neo4j": _check_neo4j,
        "elasticsearch": _check_elasticsearch,
        "broker": _check_broker,
    }


async def _run_check(
    name: str,
    check: DependencyCheck,
    timeout_seconds: float,
) -> tuple[str, str]:
    try:
        available = await asyncio.wait_for(check(), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning(
            "Readiness dependency check timed out",
            extra={"dependency": name},
        )
        return name, "timeout"
    except Exception as error:
        logger.warning(
            "Readiness dependency check failed",
            extra={
                "dependency": name,
                "error_type": type(error).__name__,
            },
        )
        return name, "error"

    if not available:
        logger.warning(
            "Readiness dependency check returned unavailable",
            extra={"dependency": name},
        )
        return name, "error"

    return name, "ok"


async def check_required_dependencies_with_timeout(
    checks: Mapping[str, DependencyCheck] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, str]:
    dependency_checks = dict(_default_checks() if checks is None else checks)
    timeout = (
        settings.READINESS_CHECK_TIMEOUT_SECONDS
        if timeout_seconds is None
        else timeout_seconds
    )

    results = await asyncio.gather(
        *(
            _run_check(name, check, timeout)
            for name, check in dependency_checks.items()
        )
    )
    return dict(results)
