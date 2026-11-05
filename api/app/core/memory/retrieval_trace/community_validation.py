"""Request-local validation snapshots; never broadcast through public agent stages."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from app.core.memory.models.community_retrieval_models import CommunityRetrievalTrace

_collector: ContextVar[list[dict] | None] = ContextVar("community_validation", default=None)
COMMUNITY_STAGES = ("community_searched", "community_scenes_searched", "community_evidence_packed")


@contextmanager
def community_validation_collector():
    events: list[dict] = []
    token = _collector.set(events)
    try:
        yield events
    finally:
        _collector.reset(token)


def project_community_trace(trace: CommunityRetrievalTrace) -> dict:
    """Full stage/answer snapshot; the SSE retrieval_trace is projected separately."""
    communities = [
        {"id": item["id"], "node_type": "SceneCommunity",
         "topic_scope": item["topic_scope"], "rank": rank, "score": item["score"]}
        for rank, item in enumerate(trace.selected_communities, 1)
    ]
    candidates = [
        {"id": item["id"], "node_type": "SceneSummary", "topic_scope": item["topic_scope"],
         "community_id": group["community_id"], "rank": rank, "score": item["score"]}
        for group in trace.members
        for rank, item in enumerate(group["candidates"], 1)
    ]
    scenes = {item["id"]: {**item, "community_id": group["community_id"]}
              for group in trace.members for item in group["candidates"]}
    items = [{"id": node_id, "node_type": "SceneSummary", "community_id": scenes[node_id]["community_id"],
              "title": scenes[node_id]["topic_scope"],
              "content": scenes[node_id]["content"], "score": scenes[node_id]["score"],
              "rank": rank, "evidence_no": rank}
             for rank, node_id in enumerate(trace.evidence_ids, 1)]
    return {
        "kind": "community", "status": trace.status, "backend": str(trace.backend or "neo4j").lower(),
        "semantic_degraded": trace.semantic_degraded,
        "score_type": "fulltext" if trace.semantic_degraded else "cosine",
        "empty_reason": trace.empty_reason, "duration_ms": trace.stage_duration_ms.get("total", 0),
        "communities": communities, "scenes": candidates,
        "packing": {
            "merged_scene_count": len(trace.merged_ranking),
            "evidence_count": len(items), "final_tokens": trace.final_tokens,
            "context_token_budget": trace.parameters["context_token_budget"],
            "budget_tokenizer": trace.parameters["budget_tokenizer"],
            "actions": [dict(action) for action in trace.packing_actions],
        },
        "items": items,
        "counts": {
            "matched_community_count": trace.matched_community_count,
            "recalled_community_count": trace.recalled_community_count,
            "selected_community_count": len(communities),
            "recalled_scene_count": sum(group["recalled_scene_count"] for group in trace.members),
            "selected_scene_count": sum(len(group["candidates"]) for group in trace.members),
            "evidence_count": len(items),
        },
    }


def emit_community_stage(trace: CommunityRetrievalTrace, stage: str, status: str) -> None:
    events = _collector.get()
    if events is None:
        return
    snapshot = project_community_trace(trace)
    if stage == "community_searched":
        data = {"items": snapshot["communities"]}
    elif stage == "community_scenes_searched":
        data = {"items": snapshot["scenes"]}
    else:
        data = {"items": snapshot["items"]}
    events.append(deepcopy({"stage": stage, "status": status, "data": data}))
