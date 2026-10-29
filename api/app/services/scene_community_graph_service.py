import asyncio
import logging
from time import perf_counter
from typing import Protocol
from uuid import UUID

from app.services.scene_community_graph_access import AccessResolver
from app.services.scene_community_graph_assembler import assemble
from app.cache.memory.scene_community_graph_cache import RedisDataCache, encode_data
from app.services.scene_community_graph_types import AuthorizedUser, Options, capacity_error
from app.schemas.scene_community_graph_schema import GraphData
from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.repositories.end_user_repository import SceneGraphUserLookup
from app.repositories.neo4j.scene_community_graph_repository import Neo4jRepository, SharedSceneGraphConnector
from app.aioRedis import get_thread_safe_redis
from functools import lru_cache

logger = logging.getLogger(__name__)


class Repository(Protocol):
    async def communities(self, user: AuthorizedUser, limit: int) -> list[dict]: ...
    async def scenes(self, user: AuthorizedUser, limit: int) -> list[dict]: ...


class Cache(Protocol):
    async def get(self, user: AuthorizedUser) -> GraphData | None: ...
    async def put(self, user: AuthorizedUser, data: GraphData) -> None: ...


class SceneGraphService:
    def __init__(self, resolver: AccessResolver, repository: Repository,
                 options: Options, cache: Cache | None = None):
        self.resolver = resolver
        self.repository = repository
        self.options = options
        self.cache = cache

    async def get_graph(self, workspace_id: UUID, requested_id: str) -> GraphData:
        started = perf_counter()
        try:
            # Deliberately outside the cache: recheck authorization on every call.
            user = await self.resolver.resolve(workspace_id, requested_id)
        except BusinessException:
            raise
        except Exception as exc:
            logger.error("scene_graph.user_lookup_failed", extra={"error_type": type(exc).__name__})
            raise BusinessException("场景社区图谱服务暂不可用", BizCode.SERVICE_UNAVAILABLE) from exc

        if not self.options.enabled:
            raise BusinessException("场景社区图谱服务尚未启用", BizCode.SERVICE_UNAVAILABLE)
        if self.options.cache_enabled and self.cache is not None:
            cached = await self.cache.get(user)
            if cached is not None:
                self._check_capacity(cached)
                logger.info("scene_graph.complete cache=hit elapsed_ms=%.2f", (perf_counter() - started) * 1000)
                return cached

        data = await self._read(user)
        if self.options.cache_enabled and self.cache is not None:
            await self.cache.put(user, data)
        logger.info(
            "scene_graph.complete cache=miss communities=%d scenes=%d unassigned=%d elapsed_ms=%.2f",
            data.statistics.total_scene_communities, data.statistics.total_scene_summaries,
            data.statistics.unassigned_scene_summaries, (perf_counter() - started) * 1000,
        )
        return data

    async def _read(self, user: AuthorizedUser) -> GraphData:
        try:
            async with asyncio.timeout(self.options.read_timeout):
                async with asyncio.TaskGroup() as tasks:
                    communities = tasks.create_task(self.repository.communities(user, self.options.max_communities + 1))
                    scenes = tasks.create_task(self.repository.scenes(user, self.options.max_scenes + 1))
        except Exception as exc:
            # TaskGroup waits for sibling cancellation/cleanup before raising.
            logger.error("scene_graph.read_failed", extra={"error_type": type(exc).__name__})
            raise BusinessException("场景社区图谱服务暂不可用", BizCode.SERVICE_UNAVAILABLE) from exc

        if len(communities.result()) > self.options.max_communities or len(scenes.result()) > self.options.max_scenes:
            raise capacity_error()
        data = assemble(communities.result(), scenes.result())
        self._check_capacity(data)
        return data

    def _check_capacity(self, data: GraphData) -> None:
        if (data.statistics.total_scene_communities > self.options.max_communities
                or data.statistics.total_scene_summaries > self.options.max_scenes
                or len(encode_data(data)) > self.options.max_bytes):
            raise capacity_error()


@lru_cache(maxsize=1)
def get_scene_community_graph_service() -> SceneGraphService:
    """Build the shared service from central settings and existing connectors."""
    options = Options(
        enabled=settings.ENABLE_SCENE_COMMUNITY_GRAPH,
        cache_enabled=settings.SCENE_GRAPH_CACHE_ENABLED,
        cache_ttl=settings.SCENE_GRAPH_CACHE_TTL_SECONDS,
        max_communities=settings.SCENE_GRAPH_MAX_COMMUNITIES,
        max_scenes=settings.SCENE_GRAPH_MAX_SCENES,
        max_bytes=settings.SCENE_GRAPH_MAX_DATA_BYTES,
        read_timeout=settings.SCENE_GRAPH_READ_BUDGET_SECONDS,
        db_timeout=settings.SCENE_GRAPH_DB_TIMEOUT_SECONDS,
        cache_timeout=settings.SCENE_GRAPH_CACHE_TIMEOUT_SECONDS,
        max_merge_hops=settings.SCENE_GRAPH_MAX_MERGE_HOPS,
    )
    return SceneGraphService(
        AccessResolver(SceneGraphUserLookup(), max_hops=options.max_merge_hops),
        Neo4jRepository(SharedSceneGraphConnector(), transaction_timeout=options.db_timeout),
        options,
        RedisDataCache(get_thread_safe_redis, options),
    )
