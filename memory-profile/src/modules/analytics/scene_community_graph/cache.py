"""Bounded graph data cache reusing the profile runtime's Redis client."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.modules.analytics.scene_community_graph.schemas import GraphData
from src.modules.analytics.scene_community_graph.types import AuthorizedUser, Options

logger = logging.getLogger(__name__)


def encode_data(data: GraphData) -> bytes:
    return json.dumps(
        data.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


class RedisDataCache:
    def __init__(self, client_factory: Callable[[], Awaitable[Any]], options: Options):
        self.client_factory = client_factory
        self.options = options

    def key(self, user: AuthorizedUser) -> str:
        policy = (
            f"{self.options.max_communities}-{self.options.max_scenes}-{self.options.max_bytes}"
        )
        return (
            "cache:memory:scene-community-graph:v2-ms:"
            f"{user.workspace_id}:{user.end_user_id}:{policy}"
        )

    async def get(self, user: AuthorizedUser) -> GraphData | None:
        try:
            async with asyncio.timeout(self.options.cache_timeout):
                client = await self.client_factory()
                raw = await client.get(self.key(user))
        except Exception:
            logger.warning("scene_graph.cache_get_failed")
            return None
        if raw is None:
            return None
        try:
            if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > self.options.max_bytes:
                raise ValueError("oversized cache")
            data = GraphData.model_validate_json(raw)
            if (
                data.statistics.total_scene_summaries > self.options.max_scenes
                or data.statistics.total_scene_communities > self.options.max_communities
            ):
                raise ValueError("oversized cache")
            return data
        except (ValueError, TypeError):
            logger.warning("scene_graph.invalid_cache")
            try:
                async with asyncio.timeout(self.options.cache_timeout):
                    client = await self.client_factory()
                    await client.delete(self.key(user))
            except Exception:
                logger.warning("scene_graph.cache_delete_failed")
            return None

    async def put(self, user: AuthorizedUser, data: GraphData) -> None:
        try:
            payload = encode_data(data)
            if len(payload) > self.options.max_bytes:
                return
            async with asyncio.timeout(self.options.cache_timeout):
                client = await self.client_factory()
                await client.set(self.key(user), payload, ex=self.options.cache_ttl)
        except Exception:
            logger.warning("scene_graph.cache_set_failed")
