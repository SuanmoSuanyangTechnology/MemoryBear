import asyncio
import logging
from time import perf_counter
from typing import Protocol
from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError, ProfileError
from src.common.normalization import parse_uuid
from src.common.redis_cache import redis_cache
from src.repositories.scene_community_graph import Neo4jRepository
from src.runtime import ProfileRuntime
from src.schemas.scene_community_graph import GraphData
from src.services.end_user import EndUserService
from src.services.scene_community_graph.assembler import assemble
from src.services.scene_community_graph.types import capacity_error, not_found, unavailable

logger = logging.getLogger(__name__)

MAX_COMMUNITIES = 1000
MAX_SCENES = 5000
MAX_DATA_BYTES = 10 * 1024 * 1024
READ_TIMEOUT_SECONDS = 5.0


class Repository(Protocol):
    async def communities(self, end_user_id: UUID, limit: int) -> list[dict]: ...
    async def scenes(self, end_user_id: UUID, limit: int) -> list[dict]: ...


class SceneGraphService:
    def __init__(self, end_users: EndUserService, repository: Repository):
        self.end_users = end_users
        self.repository = repository

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
            # Authorization and merge resolution must precede every cache lookup.
            resolved = await self.end_users.resolve_in_workspace(user_id, workspace_id)
        except ProfileError:
            raise
        except Exception as exc:
            logger.error("scene_graph.user_lookup_failed", extra={"error_type": type(exc).__name__})
            raise unavailable() from exc
        if resolved is None:
            raise not_found()

        data = await self._read(resolved.workspace_id, resolved.id)
        self._check_capacity(data)
        logger.info(
            "scene_graph.complete communities=%d scenes=%d unassigned=%d elapsed_ms=%.2f",
            data.statistics.total_scene_communities,
            data.statistics.total_scene_summaries,
            data.statistics.unassigned_scene_summaries,
            (perf_counter() - started) * 1000,
        )
        return data

    @redis_cache(
        ttl=60,
        prefix="memory_scene_community_graph_v3",
        skip_args=["self"],
        id_arg="end_user_id",
        return_type=GraphData,
    )
    async def _read(self, workspace_id: UUID, end_user_id: UUID) -> GraphData:
        """Cache only authorized graph reads, isolated by workspace and resolved user."""
        try:
            async with asyncio.timeout(READ_TIMEOUT_SECONDS):
                async with asyncio.TaskGroup() as tasks:
                    communities = tasks.create_task(
                        self.repository.communities(end_user_id, MAX_COMMUNITIES + 1)
                    )
                    scenes = tasks.create_task(self.repository.scenes(end_user_id, MAX_SCENES + 1))
        except Exception as exc:
            logger.error("scene_graph.read_failed", extra={"error_type": type(exc).__name__})
            raise unavailable() from exc
        if len(communities.result()) > MAX_COMMUNITIES or len(scenes.result()) > MAX_SCENES:
            raise capacity_error()
        data = assemble(communities.result(), scenes.result())
        self._check_capacity(data)
        return data

    @staticmethod
    def _check_capacity(data: GraphData) -> None:
        if (
            data.statistics.total_scene_communities > MAX_COMMUNITIES
            or data.statistics.total_scene_summaries > MAX_SCENES
            or len(data.model_dump_json().encode("utf-8")) > MAX_DATA_BYTES
        ):
            raise capacity_error()


def get_scene_community_graph_service(
    runtime: ProfileRuntime,
    end_users: EndUserService,
) -> SceneGraphService:
    """Reuse the service-owned Neo4j client and the common Redis cache decorator."""
    return SceneGraphService(end_users, Neo4jRepository(runtime.neo4j))
