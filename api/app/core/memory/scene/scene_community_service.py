"""Incremental SceneCommunity planning and persistence."""
from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import uuid4

from jinja2 import Template

from app.core.memory.models.graph_models import SceneCommunityNode, SceneSummaryNode
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.core.memory.scene.scene_community_resources import (
    SceneCommunityOntology,
    load_scene_community_ontology,
    load_scene_community_prompt,
)
from app.core.memory.storage.custom.scene_storage import (
    SceneStorage,
)
from app.db import get_db_context
from app.schemas.scene_community_schema import (
    CommunityBoundarySummaryOutput,
    CommunityJudgeOutput,
    SceneOntologyRouteOutput,
)


logger = logging.getLogger(__name__)

_O1_PROMPT = "o1_scene_ontology_route_v1.jinja2"
_P2_PROMPT = "p2_community_judge_v2.jinja2"
_P3_PROMPT = "p3_community_boundary_summary_v2.jinja2"
_BOUNDARY_FIELDS = (
    "boundary_instance_anchor",
    "boundary_lifecycle_anchor",
    "boundary_primary_matter",
    "boundary_include_rule",
    "boundary_exclude_rule",
)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if not left_norm or not right_norm:
        return -1.0
    return dot / (left_norm * right_norm)


def _community_prompt_payload(community: SceneCommunityNode) -> dict[str, Any]:
    return {
        "id": community.id,
        "category_l1": community.category_l1,
        "topic_name": community.topic_name,
        "topic_scope": community.topic_scope,
        "boundary_instance_anchor": community.boundary_instance_anchor,
        "boundary_lifecycle_anchor": community.boundary_lifecycle_anchor,
        "boundary_primary_matter": community.boundary_primary_matter,
        "boundary_include_rule": community.boundary_include_rule,
        "boundary_exclude_rule": community.boundary_exclude_rule,
        "summary": community.summary,
    }


class SceneCommunityIncrementalService:
    """Plan one complete INACTIVE batch, then commit it atomically."""

    def __init__(
        self,
        *,
        writer: SceneStorage,
        memory_config: Any,
        llm: Any | None = None,
        embedder: Any | None = None,
        ontology: SceneCommunityOntology | None = None,
    ) -> None:
        self.writer = writer
        self.memory_config = memory_config
        self.llm = llm
        self.embedder = embedder
        self.ontology = ontology

    # Shared model invocation for O1, P2, and P3.
    def _ensure_clients(self) -> tuple[Any, Any]:
        if self.llm is None or self.embedder is None:
            with get_db_context() as db:
                self.llm = ModelClientMixin.get_llm_client(
                    db,
                    self.memory_config.llm_model_id,
                    self.memory_config.tenant_id,
                )
                self.embedder = ModelClientMixin.get_embedding_client(
                    db,
                    self.memory_config.embedding_model_id,
                    self.memory_config.tenant_id,
                )
        return self.llm, self.embedder

    async def _invoke(self, prompt_name: str, payload: dict[str, Any], schema):
        llm, _ = self._ensure_clients()
        prompt = Template(load_scene_community_prompt(prompt_name)).render(
            request_payload_json=json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
        )
        result = await llm.with_structured_output(schema, strict=True).ainvoke(prompt)
        return result if isinstance(result, schema) else schema.model_validate(result)

    # Step [1/3] O1: route a SceneSummary to an ontology category.
    async def _route_scene(
        self,
        scene: SceneSummaryNode,
        ontology: SceneCommunityOntology,
    ) -> SceneOntologyRouteOutput:
        result = await self._invoke(
            _O1_PROMPT,
            {
                "current_scene": {
                    "summary": scene.content,
                    "topic_scope": scene.topic_scope,
                },
                "available_categories": list(ontology.available_categories),
            },
            SceneOntologyRouteOutput,
        )
        allowed = {category["code"] for category in ontology.available_categories}
        if result.category_l1 not in allowed:
            raise RuntimeError("O1 returned a category outside available_categories")
        return result

    # Step [2/3] P2: load, merge, and rank candidate communities.
    async def _candidates(
        self,
        *,
        end_user_id: str,
        scene: SceneSummaryNode,
        category_l1: str,
        planned: dict[str, SceneCommunityNode],
    ) -> list[SceneCommunityNode]:
        compare_all = bool(
            self.memory_config.compare_all_same_category_communities
        )
        limit = int(self.memory_config.candidate_community_limit)
        rows = await self.writer.load_candidate_communities(
            end_user_id=end_user_id,
            category_l1=category_l1,
            summary_embedding=scene.summary_embedding,
            candidate_limit=limit,
            compare_all=compare_all,
        )
        logger.info(
            "[SceneCommunity][2/3] P2 candidates fetched compare_all=%s "
            "fetched_community_count=%s",
            compare_all,
            len(rows),
        )
        ranked: dict[str, tuple[SceneCommunityNode, float]] = {}
        for row in rows:
            similarity = float(row.pop("_similarity", -1.0))
            community = SceneCommunityNode.model_validate(row)
            ranked[community.id] = (community, similarity)
        for community in planned.values():
            if community.category_l1 != category_l1:
                continue
            ranked[community.id] = (
                community,
                _cosine(scene.summary_embedding, community.summary_embedding),
            )
        ordered = sorted(
            ranked.values(),
            key=lambda item: (-item[1], item[0].id),
        )
        if not compare_all:
            ordered = ordered[:limit]
        return [community for community, _ in ordered]

    # Step [2/3] P2: assign the scene to a candidate community or create one.
    async def _judge_community(
        self,
        scene: SceneSummaryNode,
        category_l1: str,
        candidates: Sequence[SceneCommunityNode],
        ontology: SceneCommunityOntology,
    ) -> CommunityJudgeOutput:
        result = await self._invoke(
            _P2_PROMPT,
            {
                "category_boundary": ontology.category_boundary(category_l1),
                "current_scene_summary": {
                    "category_l1": category_l1,
                    "topic_scope": scene.topic_scope,
                    "summary": scene.content,
                },
                "existing_communities": [
                    _community_prompt_payload(candidate) for candidate in candidates
                ],
            },
            CommunityJudgeOutput,
        )
        candidate_ids = {candidate.id for candidate in candidates}
        if (
            result.decision == "ASSIGN_EXISTING"
            and result.target_community_id not in candidate_ids
        ):
            raise RuntimeError("P2 returned a community outside the candidate set")
        return result

    # Step [3/3] P3: establish or refresh the community boundary summary.
    async def _summarize_community(
        self,
        *,
        operation_mode: str,
        community: SceneCommunityNode,
        members: Sequence[SceneSummaryNode],
        ontology: SceneCommunityOntology,
    ) -> CommunityBoundarySummaryOutput:
        result = await self._invoke(
            _P3_PROMPT,
            {
                "operation_mode": operation_mode,
                "category_boundary": ontology.category_boundary(community.category_l1),
                "community": _community_prompt_payload(community),
                "members": [{"summary": member.content} for member in members],
            },
            CommunityBoundarySummaryOutput,
        )
        stable_values = [
            result.topic_name,
            result.topic_scope,
            *(getattr(result, field) for field in _BOUNDARY_FIELDS),
        ]
        if operation_mode == "ESTABLISH_BOUNDARY" and any(
            value is None for value in stable_values
        ):
            raise RuntimeError("P3 ESTABLISH_BOUNDARY returned an incomplete boundary")
        if operation_mode == "UPDATE_SUMMARY" and any(
            value is not None for value in stable_values
        ):
            raise RuntimeError("P3 UPDATE_SUMMARY attempted to rewrite stable boundary fields")
        return result

    # Step [3/3] P3: embed the refreshed community summary.
    async def _embed(self, text: str) -> list[float]:
        _, embedder = self._ensure_clients()
        embeddings = await embedder.aembed_documents([text])
        if not embeddings or not embeddings[0]:
            raise RuntimeError("SceneCommunity embedding is empty")
        return list(embeddings[0])

    # Batch orchestration and atomic Neo4j/outbox commit.
    async def run(self, end_user_id: str) -> dict[str, Any]:
        batch_size = int(self.memory_config.batch_trigger_count)
        rows = await self.writer.load_inactive_batch(end_user_id, batch_size)
        if len(rows) < batch_size:
            return {
                "status": "skipped",
                "reason": "incomplete_batch",
                "inactive_count": len(rows),
                "dispatch_next": False,
            }

        scenes = [SceneSummaryNode.model_validate(row) for row in rows]
        if any(
            scene.end_user_id != end_user_id
            or scene.community_status != "INACTIVE"
            or scene.community_eligibility != "ELIGIBLE"
            or not scene.topic_scope
            for scene in scenes
        ):
            raise RuntimeError("inactive SceneCommunity batch contains an invalid SceneSummary")
        ontology = self.ontology or load_scene_community_ontology()
        self._ensure_clients()

        planned: dict[str, SceneCommunityNode] = {}
        newly_created: set[str] = set()
        added_by_community: dict[str, list[SceneSummaryNode]] = defaultdict(list)
        assignments: list[dict[str, Any]] = []
        target_order: list[str] = []

        for scene in scenes:
            # Step [1/3] O1: route the scene to one ontology category.
            o1_started_at = time.monotonic()
            route = await self._route_scene(scene, ontology)
            logger.info(
                "[SceneCommunity][1/3] O1 completed user=%s scene=%s category=%s "
                "reason=%s confidence=%s elapsed_ms=%s",
                end_user_id,
                scene.id,
                route.category_l1,
                route.reason_code,
                route.confidence,
                int((time.monotonic() - o1_started_at) * 1000),
            )

            # Step [2/3] P2: select an existing community or plan a new one.
            p2_started_at = time.monotonic()
            candidates = await self._candidates(
                end_user_id=end_user_id,
                scene=scene,
                category_l1=route.category_l1,
                planned=planned,
            )
            if candidates:
                judge = await self._judge_community(
                    scene,
                    route.category_l1,
                    candidates,
                    ontology,
                )
            else:
                judge = None

            if judge is not None and judge.decision == "ASSIGN_EXISTING":
                community_id = str(judge.target_community_id)
                if community_id not in planned:
                    planned[community_id] = next(
                        candidate for candidate in candidates if candidate.id == community_id
                    )
            else:
                now = datetime.now(timezone.utc)
                community_id = str(uuid4())
                planned[community_id] = SceneCommunityNode(
                    id=community_id,
                    end_user_id=end_user_id,
                    category_l1=route.category_l1,
                    topic_scope=scene.topic_scope,
                    summary=scene.content,
                    summary_embedding=scene.summary_embedding,
                    member_count=1,
                    started_at=scene.started_at,
                    ended_at=scene.ended_at,
                    created_at=now,
                    updated_at=now,
                )
                newly_created.add(community_id)

            if community_id not in added_by_community:
                target_order.append(community_id)
            added_by_community[community_id].append(scene)
            assignments.append({
                "scene_summary_id": scene.id,
                "scene_community_id": community_id,
                "category_l1": route.category_l1,
            })
            if judge is None:
                logger.info(
                    "[SceneCommunity][2/3] P2 skipped user=%s scene=%s category=%s "
                    "community=%s candidates=0 decision=CREATE_NEW "
                    "reason=NO_CANDIDATES elapsed_ms=%s",
                    end_user_id,
                    scene.id,
                    route.category_l1,
                    community_id,
                    int((time.monotonic() - p2_started_at) * 1000),
                )
            else:
                logger.info(
                    "[SceneCommunity][2/3] P2 completed user=%s scene=%s category=%s "
                    "community=%s candidates=%s decision=%s reason=%s "
                    "confidence=%s elapsed_ms=%s",
                    end_user_id,
                    scene.id,
                    route.category_l1,
                    community_id,
                    len(candidates),
                    judge.decision,
                    judge.reason_code,
                    judge.confidence,
                    int((time.monotonic() - p2_started_at) * 1000),
                )

        persisted_targets = [
            community_id for community_id in target_order
            if community_id not in newly_created
        ]
        existing_members = await self.writer.load_community_members(
            end_user_id,
            persisted_targets,
        )
        commit_time = datetime.now(timezone.utc)
        final_communities: list[dict[str, Any]] = []

        for community_id in target_order:
            # Step [3/3] P3: refresh the community summary and its stable boundary.
            community = planned[community_id]
            prior_members = [
                SceneSummaryNode.model_validate(row)
                for row in existing_members.get(community_id, [])
            ]
            members = [*prior_members, *added_by_community[community_id]]
            if len(members) > 1:
                p3_started_at = time.monotonic()
                boundary_exists = all(
                    getattr(community, field) is not None
                    for field in _BOUNDARY_FIELDS
                )
                operation_mode = (
                    "UPDATE_SUMMARY" if boundary_exists else "ESTABLISH_BOUNDARY"
                )
                p3 = await self._summarize_community(
                    operation_mode=operation_mode,
                    community=community,
                    members=members,
                    ontology=ontology,
                )
                updates: dict[str, Any] = {
                    "summary": p3.summary,
                    "summary_embedding": await self._embed(p3.summary),
                }
                if operation_mode == "ESTABLISH_BOUNDARY":
                    updates.update({
                        "topic_name": p3.topic_name,
                        "topic_scope": p3.topic_scope,
                        **{
                            field: getattr(p3, field)
                            for field in _BOUNDARY_FIELDS
                        },
                    })
                community = community.model_copy(update=updates)
                logger.info(
                    "[SceneCommunity][3/3] P3 completed user=%s community=%s mode=%s "
                    "fetched_members=%s added_members=%s members=%s reason=%s "
                    "elapsed_ms=%s",
                    end_user_id,
                    community_id,
                    operation_mode,
                    len(prior_members),
                    len(added_by_community[community_id]),
                    len(members),
                    p3.short_reason,
                    int((time.monotonic() - p3_started_at) * 1000),
                )
            else:
                logger.info(
                    "[SceneCommunity][3/3] P3 skipped user=%s community=%s "
                    "fetched_members=%s added_members=%s members=%s "
                    "reason=SINGLE_MEMBER",
                    end_user_id,
                    community_id,
                    len(prior_members),
                    len(added_by_community[community_id]),
                    len(members),
                )

            started_at = min(
                [community.started_at, *(member.started_at for member in members)]
            )
            ended_at = max(
                [community.ended_at, *(member.ended_at for member in members)]
            )
            community = community.model_copy(update={
                "member_count": len(members),
                "started_at": started_at,
                "ended_at": ended_at,
                "updated_at": commit_time,
            })
            planned[community_id] = community
            final_communities.append(community.model_dump())

        for assignment in assignments:
            assignment["updated_at"] = commit_time
        await self.writer.commit_batch(
            end_user_id=end_user_id,
            communities=final_communities,
            assignments=assignments,
        )
        remaining = await self.writer.count_inactive(end_user_id)
        return {
            "status": "success",
            "processed": len(scenes),
            "community_count": len(final_communities),
            "remaining_inactive_count": remaining,
            "dispatch_next": remaining >= batch_size,
        }
