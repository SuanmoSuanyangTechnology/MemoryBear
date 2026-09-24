import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.memory.scene.scene_community_service import (
    SceneCommunityIncrementalService,
)


class SequencedLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def with_structured_output(self, _schema, strict=True):
        assert strict is True
        return self

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        return self.outputs.pop(0)


def scene(scene_id: str, created_at: datetime) -> dict:
    return {
        "id": scene_id,
        "end_user_id": "user-1",
        "conversation_id": "conversation-1",
        "content": f"summary {scene_id}",
        "topic_scope": "same bounded project",
        "summary_embedding": [1.0, 0.0],
        "source_message_ids": [scene_id],
        "start_message_id": scene_id,
        "end_message_id": scene_id,
        "started_at": created_at,
        "ended_at": created_at + timedelta(minutes=1),
        "turn_count": 1,
        "close_reason": "SHIFTED",
        "config_id": "config-1",
        "community_eligibility": "ELIGIBLE",
        "community_status": "INACTIVE",
        "created_at": created_at,
        "updated_at": created_at,
    }


async def test_batch_reuses_temporary_community_and_calls_p3_once(caplog):
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    writer = SimpleNamespace(
        load_inactive_batch=AsyncMock(return_value=[scene("s1", now), scene("s2", now + timedelta(minutes=2))]),
        load_candidate_communities=AsyncMock(return_value=[]),
        load_community_members=AsyncMock(return_value={}),
        commit_batch=AsyncMock(),
        count_inactive=AsyncMock(return_value=0),
    )
    llm = SequencedLLM([
        {
            "category_l1": "work_career",
            "reason_code": "CLEAR_MAIN_CATEGORY",
            "short_reason": "work",
            "confidence": "HIGH",
        },
        {
            "category_l1": "work_career",
            "reason_code": "CLEAR_MAIN_CATEGORY",
            "short_reason": "work",
            "confidence": "HIGH",
        },
        {
            "decision": "ASSIGN_EXISTING",
            "target_community_id": "placeholder",
            "reason_code": "SAME_BOUNDED_INSTANCE",
            "short_reason": "same project",
            "confidence": "HIGH",
        },
        {
            "topic_name": "Project",
            "topic_scope": "same bounded project",
            "boundary_instance_anchor": "project A",
            "boundary_lifecycle_anchor": "current delivery",
            "boundary_primary_matter": "ship project A",
            "boundary_include_rule": "project A delivery",
            "boundary_exclude_rule": "other projects",
            "summary": "combined summary",
            "short_reason": "boundary established",
            "confidence": "HIGH",
        },
    ])

    original_judge = SceneCommunityIncrementalService._judge_community

    async def judge_with_runtime_id(self, scene, category_l1, candidates, ontology):
        llm.outputs[0]["target_community_id"] = candidates[0].id
        return await original_judge(self, scene, category_l1, candidates, ontology)

    service = SceneCommunityIncrementalService(
        writer=writer,
        memory_config=SimpleNamespace(
            batch_trigger_count=2,
            candidate_community_limit=3,
            compare_all_same_category_communities=True,
        ),
        llm=llm,
        embedder=SimpleNamespace(
            aembed_documents=AsyncMock(return_value=[[0.8, 0.2]])
        ),
    )
    service._judge_community = judge_with_runtime_id.__get__(service)

    with caplog.at_level(
        logging.INFO,
        logger="app.core.memory.scene.scene_community_service",
    ):
        result = await service.run("user-1")

    assert result["status"] == "success"
    assert result["processed"] == 2
    assert result["community_count"] == 1
    assert len(llm.prompts) == 4
    commit = writer.commit_batch.await_args.kwargs
    assert len(commit["communities"]) == 1
    assert commit["communities"][0]["member_count"] == 2
    assert commit["communities"][0]["summary"] == "combined summary"
    assert {
        item["scene_community_id"] for item in commit["assignments"]
    } == {commit["communities"][0]["id"]}
    assert "compare_all=True fetched_community_count=0" in caplog.text
    assert "fetched_members=0 added_members=2 members=2" in caplog.text


async def test_incomplete_batch_does_not_call_models_or_write():
    writer = SimpleNamespace(
        load_inactive_batch=AsyncMock(return_value=[]),
        commit_batch=AsyncMock(),
    )
    service = SceneCommunityIncrementalService(
        writer=writer,
        memory_config=SimpleNamespace(batch_trigger_count=2),
        llm=SequencedLLM([]),
        embedder=SimpleNamespace(),
    )

    result = await service.run("user-1")

    assert result["reason"] == "incomplete_batch"
    writer.commit_batch.assert_not_awaited()


async def test_single_member_new_community_skips_p3():
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    writer = SimpleNamespace(
        load_inactive_batch=AsyncMock(return_value=[scene("s1", now)]),
        load_candidate_communities=AsyncMock(return_value=[]),
        load_community_members=AsyncMock(return_value={}),
        commit_batch=AsyncMock(),
        count_inactive=AsyncMock(return_value=0),
    )
    llm = SequencedLLM([{
        "category_l1": "work_career",
        "reason_code": "CLEAR_MAIN_CATEGORY",
        "short_reason": "work",
        "confidence": "HIGH",
    }])
    embedder = SimpleNamespace(aembed_documents=AsyncMock())
    service = SceneCommunityIncrementalService(
        writer=writer,
        memory_config=SimpleNamespace(
            batch_trigger_count=1,
            candidate_community_limit=3,
            compare_all_same_category_communities=False,
        ),
        llm=llm,
        embedder=embedder,
    )

    result = await service.run("user-1")

    assert result["status"] == "success"
    assert len(llm.prompts) == 1
    embedder.aembed_documents.assert_not_awaited()
    community = writer.commit_batch.await_args.kwargs["communities"][0]
    assert community["member_count"] == 1
    assert community["topic_name"] is None
    assert community["boundary_instance_anchor"] is None
