"""Elasticsearch cosine/full-text retrieval with filtered and validated results."""

import math
from collections.abc import Mapping
from numbers import Real

from app.core.memory.exceptions import CommunityRetrievalError
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.provider.elasticsearch.index.definitions import get_embedding_field_name, get_index_name


class CommunityRetrievalRepository:
    def __init__(self, client, end_user_id: str, *, policy):
        if not end_user_id:
            raise CommunityRetrievalError("invalid_ownership")
        self.client = client
        self.end_user_id = end_user_id
        self.policy = policy
        self.database_requests = 0

    def _validate(self, node: dict, *, scene: bool, community_id: str | None = None) -> None:
        if not isinstance(node, Mapping) or not isinstance(node.get("id"), str) or not node["id"]:
            raise CommunityRetrievalError("invalid_node_id")
        if node.get("end_user_id") != self.end_user_id:
            raise CommunityRetrievalError("invalid_ownership")
        if node.get("delete_at") is not None:
            raise CommunityRetrievalError("data_changed")
        if not isinstance(node.get("content" if scene else "summary"), str):
            raise CommunityRetrievalError("invalid_node_data")
        if scene and (
            node.get("community_status") != "ACTIVE"
            or node.get("community_eligibility") != "ELIGIBLE"
            or node.get("scene_community_id") != community_id
        ):
            raise CommunityRetrievalError("invalid_ownership")

    @staticmethod
    def _check_response(result: Mapping) -> None:
        if result.get("timed_out"):
            raise CommunityRetrievalError("scan_timeout")
        if result.get("_shards", {}).get("failed", 0):
            raise CommunityRetrievalError("dependency_failed")

    async def _search(self, value: list[float] | str, *, community_id: str | None = None) -> tuple[list[tuple[float, dict]], int]:
        fulltext = isinstance(value, str)
        scene = community_id is not None
        label = MemoryNodeType.SCENE_SUMMARY if scene else MemoryNodeType.SCENE_COMMUNITY
        policy = self.policy
        limit = policy.member_top_k if scene else policy.community_top_k
        threshold = policy.scene_min_cosine if scene else policy.community_min_cosine
        filters = [{"term": {"end_user_id": self.end_user_id}}]
        if scene:
            filters.extend([
                {"term": {"scene_community_id": community_id}},
                {"term": {"community_status": "ACTIVE"}},
                {"term": {"community_eligibility": "ELIGIBLE"}},
            ])
        filtered_query = {"filter": filters, "must_not": [{"exists": {"field": "delete_at"}}]}
        if fulltext:
            filtered_query["must"] = [{"match": {"content" if scene else "summary": value}}]
            query = {"bool": filtered_query}
            score_options = {}
        else:
            try:
                embedding_field = get_embedding_field_name(label, len(value))
            except ValueError as exc:
                raise CommunityRetrievalError("invalid_vector") from exc
            query = {"script_score": {
                "query": {"bool": filtered_query},
                "script": {
                    "source": "cosineSimilarity(params.query_vector, params.embedding_field) + 1.0",
                    "params": {"query_vector": value, "embedding_field": embedding_field},
                },
            }}
            # ES scores must be nonnegative; remove this shift before returning evidence scores.
            score_options = {"min_score": threshold + 1.0}
        self.database_requests += 1
        result = await self.client.search(
            index=get_index_name(label),
            query=query,
            **score_options,
            size=limit,
            sort=[{"_score": "desc"}, {"id": "asc"}],
            track_total_hits=True,
            allow_partial_search_results=False,
            timeout=f"{policy.retrieval_timeout_seconds}s",
        )
        self._check_response(result)
        hits = result["hits"]
        candidates = []
        for hit in hits["hits"]:
            node, score = hit.get("_source"), hit.get("_score")
            self._validate(node, scene=scene, community_id=community_id)
            if hit.get("_id") != node["id"]:
                raise CommunityRetrievalError("invalid_node_id")
            if (not isinstance(score, Real) or isinstance(score, bool) or not math.isfinite(score)
                    or (score < 0 if fulltext else not threshold + 1.0 - 1e-6 <= score <= 2.0 + 1e-6)):
                raise CommunityRetrievalError("invalid_search_score")
            candidates.append((float(score) if fulltext else min(1.0, max(-1.0, float(score) - 1.0)), node))
        return sorted(candidates, key=lambda item: (-item[0], item[1]["id"])), hits["total"]["value"]

    async def communities(self, vector: list[float]) -> tuple[list[tuple[float, dict]], int]:
        return await self._search(vector)

    async def members(self, community: dict, vector: list[float]) -> tuple[list[tuple[float, dict]], int]:
        return await self._search(vector, community_id=community["id"])

    async def communities_fulltext(self, query: str) -> tuple[list[tuple[float, dict]], int]:
        return await self._search(query)

    async def members_fulltext(self, community: dict, query: str) -> tuple[list[tuple[float, dict]], int]:
        return await self._search(query, community_id=community["id"])
