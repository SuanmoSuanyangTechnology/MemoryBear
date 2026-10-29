import asyncio
import logging
from time import perf_counter
from typing import Protocol
from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError, ProfileError
from src.common.normalization import parse_uuid
from src.modules.analytics.repository.scene_community_graph import Neo4jRepository
from src.modules.analytics.scene_community_graph.assembler import assemble
from src.modules.analytics.scene_community_graph.cache import RedisDataCache, encode_data
from src.modules.analytics.scene_community_graph.schemas import GraphData
from src.modules.analytics.scene_community_graph.types import (
    AuthorizedUser,
    Options,
    capacity_error,
    not_found,
    unavailable,
)
from src.modules.shared.end_user_service import EndUserService
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


class Repository(Protocol):
    async def communities(self, user: AuthorizedUser, limit: int) -> list[dict]: ...
    async def scenes(self, user: AuthorizedUser, limit: int) -> list[dict]: ...


class Cache(Protocol):
    async def get(self, user: AuthorizedUser) -> GraphData | None: ...
    async def put(self, user: AuthorizedUser, data: GraphData) -> None: ...


class SceneGraphService:
    def __init__(
        self,
        end_users: EndUserService,
        repository: Repository,
        options: Options,
        cache: Cache | None = None,
    ):
        self.end_users = end_users
        self.repository = repository
        self.options = options
        self.cache = cache

    async def get_graph(self, workspace_id: UUID, requested_id: str) -> GraphData:
        started = perf_counter()
        user_id = parse_uuid(requested_id)
        if user_id is None:
            raise LocalizedProfileError(
                BizCode.INVALID_USER_ID,
                "analytics.scene_graph.invalid_user_id",
                detail_key="analytics.scene_graph.invalid_user_id",
                status_code=400,
            )
        try:
            # Reuse the graph endpoints' workspace/merge rule before every cache lookup.
            resolved = await self.end_users.resolve_in_workspace(user_id, workspace_id)
        except ProfileError:
            raise
        except Exception as exc:
            logger.error("scene_graph.user_lookup_failed", extra={"error_type": type(exc).__name__})
            raise unavailable() from exc

        if resolved is None:
            raise not_found()
        user = AuthorizedUser(resolved.workspace_id, resolved.id)

        if not self.options.enabled:
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.scene_graph.disabled",
                detail_key="analytics.scene_graph.disabled",
                status_code=503,
            )
        if self.options.cache_enabled and self.cache is not None:
            cached = await self.cache.get(user)
            if cached is not None:
                self._check_capacity(cached)
                logger.info(
                    "scene_graph.complete cache=hit elapsed_ms=%.2f",
                    (perf_counter() - started) * 1000,
                )
                return cached

        data = await self._read(user)
        if self.options.cache_enabled and self.cache is not None:
            await self.cache.put(user, data)
        logger.info(
            "scene_graph.complete cache=miss "
            "communities=%d scenes=%d unassigned=%d elapsed_ms=%.2f",
            data.statistics.total_scene_communities,
            data.statistics.total_scene_summaries,
            data.statistics.unassigned_scene_summaries,
            (perf_counter() - started) * 1000,
        )
        return data

    async def _read(self, user: AuthorizedUser) -> GraphData:
        try:
            async with asyncio.timeout(self.options.read_timeout):
                async with asyncio.TaskGroup() as tasks:
                    communities = tasks.create_task(
                        self.repository.communities(user, self.options.max_communities + 1)
                    )
                    scenes = tasks.create_task(
                        self.repository.scenes(user, self.options.max_scenes + 1)
                    )
        except Exception as exc:
            # TaskGroup waits for sibling cancellation/cleanup before raising.
            logger.error("scene_graph.read_failed", extra={"error_type": type(exc).__name__})
            raise unavailable() from exc

        if (
            len(communities.result()) > self.options.max_communities
            or len(scenes.result()) > self.options.max_scenes
        ):
            raise capacity_error()
        data = assemble(communities.result(), scenes.result())
        self._check_capacity(data)
        return data

    def _check_capacity(self, data: GraphData) -> None:
        if (
            data.statistics.total_scene_communities > self.options.max_communities
            or data.statistics.total_scene_summaries > self.options.max_scenes
            or len(encode_data(data)) > self.options.max_bytes
        ):
            raise capacity_error()


def get_scene_community_graph_service(
    runtime: ProfileRuntime, end_users: EndUserService,
) -> SceneGraphService:
    """Compose stateless graph logic with the service-owned pooled clients."""
    settings = runtime.settings
    options = Options(
        enabled=settings.enable_scene_community_graph,
        cache_enabled=settings.scene_graph_cache_enabled,
        cache_ttl=settings.scene_graph_cache_ttl_seconds,
        max_communities=settings.scene_graph_max_communities,
        max_scenes=settings.scene_graph_max_scenes,
        max_bytes=settings.scene_graph_max_data_bytes,
        read_timeout=settings.scene_graph_read_budget_seconds,
        db_timeout=settings.scene_graph_db_timeout_seconds,
        cache_timeout=settings.scene_graph_cache_timeout_seconds,
    )
    return SceneGraphService(
        end_users,
        Neo4jRepository(runtime.neo4j, transaction_timeout=options.db_timeout),
        options,
        RedisDataCache(runtime.redis.client, options),
    )
