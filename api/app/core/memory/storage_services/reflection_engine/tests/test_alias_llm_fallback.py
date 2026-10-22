import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from app.core.memory.storage_services.reflection_engine import layer2_inspector as inspector_module
from app.core.memory.storage_services.reflection_engine.deterministic import alias_merger
from app.core.memory.storage_services.reflection_engine.llm.alias_belongs_judge import (
    AliasBatchJudgeOutput, AliasJudgeItem, judge_alias_belongs,
)
from app.core.memory.storage_services.reflection_engine.retry_registry import (
    completion_of_layer2, model_types_of_layer2, reason_codes_of_layer2,
    select_primary_model_type, select_primary_reason, skipped_steps_of_layer2,
)


def candidate(alias_id="a", target_id="target", **updates):
    return {
        "alias_id": alias_id, "target_id": target_id, "alias_name": f"别名{alias_id}",
        "alias_entity_type": "人物", "alias_description": "昵称", "alias_description_summary": "",
        "alias_aliases": ["阿明"], "target_name": f"张明{target_id}",
        "target_entity_type": "人物", "target_description": "工程师", "target_description_summary": "",
        "target_aliases": [], **updates,
    }


def llm_response(*scores):
    return AliasBatchJudgeOutput(results=[
        AliasJudgeItem(alias_index=i, confidence=score, reason=f"判断{i}")
        for i, score in enumerate(scores, 1)
    ])


def bert_response(merge=0.8, drop=0.2):
    return {"results": [
        {"index": 1, "document": {"text": "drop"}, "relevance_score": drop},
        {"index": 0, "document": {"text": "merge"}, "relevance_score": merge},
    ]}


@pytest.fixture
def run_state(monkeypatch):
    for key, value in {
        "BERT_URL": "", "ALIAS_RECOGNITION_API_KEY": "",
        "ALIAS_RECOGNITION_MODEL": "alias-recognition",
        "ALIAS_RECOGNITION_TIMEOUT_SECONDS": 1,
        "ALIAS_RECOGNITION_CONCURRENCY": 2,
    }.items():
        monkeypatch.setattr(inspector_module.settings, key, value)
    state = SimpleNamespace(rows=[], snapshots={}, changes=[], http_clients=[])
    state.llm = SimpleNamespace(model_name="fallback-model", call_structured=AsyncMock(return_value=llm_response(0.95)))
    state.repo = SimpleNamespace(create=Mock())
    state.inspector = inspector_module.Layer2Inspector(None, state.llm, lambda: state.repo)
    state.collect = AsyncMock(side_effect=lambda *_args: state.rows)
    state.drop = AsyncMock(side_effect=lambda _connector, _user, ids, **_kwargs: len(ids))
    state.merge = AsyncMock(side_effect=lambda _connector, _user, alias_ids, **_kwargs: {
        "alias_merged": len(set(alias_ids)), "edges_redirected": 0,
        "alias_nodes_deleted": len(set(alias_ids)), "pg_synced": True, "errors": {},
    })
    monkeypatch.setattr(alias_merger, "collect_alias_candidates", state.collect)
    monkeypatch.setattr(alias_merger, "drop_alias_edges", state.drop)
    monkeypatch.setattr(alias_merger, "merge_alias_belongs_to", state.merge)
    monkeypatch.setattr(state.inspector, "_get_reflection_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(state.inspector, "_snap", lambda _problem, stage, data: state.snapshots.update({stage: data}))
    monkeypatch.setattr(state.inspector, "_snap_changes", lambda _problem, data: state.changes.extend(data))

    real_client = httpx.AsyncClient

    def install_transport(handler):
        monkeypatch.setattr(inspector_module.settings, "BERT_URL", "https://example.test/v1/rerank")
        monkeypatch.setattr(inspector_module.settings, "ALIAS_RECOGNITION_API_KEY", "test-key")

        def factory(**kwargs):
            client = real_client(transport=httpx.MockTransport(handler), **kwargs)
            state.http_clients.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    state.install_transport = install_transport
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("score,expected", [(0.89, "drop"), (0.9, "merge"), (0.95, "merge")])
@pytest.mark.parametrize("missing", ["BERT_URL", "ALIAS_RECOGNITION_API_KEY", "ALIAS_RECOGNITION_MODEL"])
async def test_missing_bert_configuration_falls_back_with_original_threshold(run_state, monkeypatch, score, expected, missing):
    state = run_state
    state.install_transport(lambda _request: pytest.fail("unconfigured BERT must not request"))
    monkeypatch.setattr(inspector_module.settings, missing, "")
    state.rows = [candidate()]
    state.llm.call_structured.return_value = llm_response(score)
    result = await state.inspector._run_alias_merge("user")
    assert state.llm.call_structured.await_count == 1
    assert result["merge_count"] == (expected == "merge")
    assert result["drop_count"] == (expected == "drop")
    assert result["llm_fallback_count"] == 1
    assert result["business_failure_count"] == 0
    assert result["reason_codes"] == result["model_types"] == []
    assert completion_of_layer2({"alias_merge": result}) == "full"
    log = state.repo.create.call_args.kwargs
    assert log["confidence"] == score
    assert log["trigger_detail"]["decision_source"] == "llm_fallback"
    assert log["trigger_detail"]["llm_threshold"] == 0.9
    assert log["trigger_detail"]["score_kind"] == "llm_confidence"
    assert "merge_score" not in log["trigger_detail"]
    assert "drop_score" not in log["trigger_detail"]
    assert log["execution_detail"]["model"] == "fallback-model"
    assert [step["type"] for step in log["execution_detail"]["steps"]] == ["prompt", "bert", "llm", "write"]
    assert "LLM降级" in log["solution_detail"]["title"]
    snap = state.snapshots["2_bert_result"]["decided"][0]
    assert snap["llm_threshold"] == 0.9 and snap["decision_source"] == "llm_fallback"
    assert "merge_score" not in snap


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "network", "http", "json", "labels", "scores"])
async def test_technical_bert_failures_use_llm(run_state, failure):
    state = run_state

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        if failure == "network":
            raise httpx.ConnectError("test connection failure", request=request)
        if failure == "http":
            return httpx.Response(503)
        if failure == "json":
            return httpx.Response(200, content=b"not json")
        if failure == "labels":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json=bert_response(True, 0.2))

    state.install_transport(handler)
    state.rows = [candidate()]
    result = await state.inspector._run_alias_merge("user")
    assert result["merge_count"] == 1
    assert result["llm_fallback_count"] == 1
    assert result["business_failure_count"] == 0
    assert state.llm.call_structured.await_count == 1
    assert all(client.is_closed for client in state.http_clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold,score,expected", [(0.8, 0.85, "merge"), (0.95, 0.9, "drop"), (0.95, 0.95, "merge")])
async def test_configured_llm_threshold_controls_decision_and_is_logged(run_state, threshold, score, expected):
    state = run_state
    state.inspector.config = inspector_module.ReflectionConfig(alias_merge={"confidence_threshold": threshold})
    state.rows = [candidate()]
    state.llm.call_structured.return_value = llm_response(score)
    result = await state.inspector._run_alias_merge("user")
    assert result["merge_count"] == (expected == "merge")
    assert result["drop_count"] == (expected == "drop")
    assert state.repo.create.call_args.kwargs["trigger_detail"]["llm_threshold"] == threshold
    assert state.snapshots["2_bert_result"]["decided"][0]["llm_threshold"] == threshold


@pytest.mark.asyncio
@pytest.mark.parametrize("scores,expected", [((0.8, 0.2), "merge"), ((0.2, 0.8), "drop"), ((0.5, 0.5), None)])
async def test_valid_bert_result_and_tie_do_not_call_llm(run_state, scores, expected):
    state = run_state
    state.inspector.config = inspector_module.ReflectionConfig(alias_merge={"confidence_threshold": 0.99})
    state.install_transport(lambda _request: httpx.Response(200, json=bert_response(*scores)))
    state.rows = [candidate()]
    result = await state.inspector._run_alias_merge("user")
    state.llm.call_structured.assert_not_awaited()
    assert result["llm_fallback_count"] == 0
    assert result["merge_count"] == (expected == "merge")
    assert result["drop_count"] == (expected == "drop")
    if expected is None:
        assert result["status"] == "partial"
        assert result["deferred_reasons"] == ["ambiguous_result"]
        state.repo.create.assert_not_called()
    else:
        assert state.repo.create.call_args.kwargs["trigger_detail"]["decision_source"] == "bert"


@pytest.mark.asyncio
async def test_rule_and_invalid_input_do_not_call_either_model(run_state):
    state = run_state
    state.install_transport(lambda _request: pytest.fail("invalid input must not request BERT"))
    state.rows = [candidate("rule", alias_name=" 张工 ", target_aliases=["张工"]),
                  candidate("invalid", alias_name="")]
    result = await state.inspector._run_alias_merge("user")
    state.llm.call_structured.assert_not_awaited()
    assert result["merge_count"] == 1
    assert result["llm_fallback_count"] == 0
    assert result["deferred_reasons"] == ["empty_name"]
    assert state.repo.create.call_args.kwargs["trigger_detail"]["decision_source"] == "rule"


@pytest.mark.asyncio
async def test_only_failed_candidates_are_grouped_by_target_with_correct_mapping(run_state):
    state = run_state
    state.rows = [candidate("a"), candidate("bert"), candidate("b"), candidate("c", "other"), candidate("tie")]
    def handler(request):
        name = json.loads(json.loads(request.content)["query"])["alias_name"]
        if name == "别名bert":
            return httpx.Response(200, json=bert_response(0.2, 0.8))
        if name == "别名tie":
            return httpx.Response(200, json=bert_response(0.5, 0.5))
        return httpx.Response(503)

    state.install_transport(handler)
    state.llm.call_structured.side_effect = [
        AliasBatchJudgeOutput(results=[AliasJudgeItem(alias_index=2, confidence=0.89),
                                      AliasJudgeItem(alias_index=1, confidence=0.91)]),
        llm_response(0.95),
    ]
    result = await state.inspector._run_alias_merge("user")
    assert state.llm.call_structured.await_count == 2
    prompts = [call.args[0][0]["content"] for call in state.llm.call_structured.await_args_list]
    assert "别名a" in prompts[0] and "别名b" in prompts[0] and "阿明" in prompts[0]
    assert "别名bert" not in prompts[0] and "别名tie" not in prompts[0]
    assert "别名c" in prompts[1] and "张明other" in prompts[1]
    assert state.merge.call_args.kwargs["alias_ids"] == ["a", "c"]
    assert state.drop.call_args.args[2] == ["bert", "b"]
    assert result["llm_fallback_count"] == 3 and result["business_failure_count"] == 0
    assert result["deferred_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "wrong_type", "missing", "nonfinite", "out_of_range", "duplicate", "no_client"])
async def test_failed_llm_fallback_preserves_candidate_and_enters_retry(run_state, failure):
    state = run_state
    state.rows = [candidate()]
    if failure == "exception":
        state.llm.call_structured.side_effect = RuntimeError("provider private text")
    elif failure == "wrong_type":
        state.llm.call_structured.return_value = {"results": []}
    elif failure == "missing":
        state.llm.call_structured.return_value = llm_response()
    elif failure == "nonfinite":
        state.llm.call_structured.return_value = llm_response(float("nan"))
    elif failure == "out_of_range":
        state.llm.call_structured.return_value = llm_response(1.5)
    elif failure == "duplicate":
        state.llm.call_structured.return_value = AliasBatchJudgeOutput(results=[
            AliasJudgeItem(alias_index=1, confidence=0.95), AliasJudgeItem(alias_index=1, confidence=0.1)])
    else:
        state.inspector.llm_client = None
    result = await state.inspector._run_alias_merge("user")
    assert state.merge.call_args.kwargs["alias_ids"] == []
    assert state.drop.call_args.args[2] == []
    state.repo.create.assert_not_called()
    assert result["status"] == "partial" and result["business_failure_count"] == 1
    assert result["llm_fallback_failed_count"] == 1
    assert result["model_types"] == ["llm"]
    wrapped = {"alias_merge": result}
    assert completion_of_layer2(wrapped) == "partial"
    codes = reason_codes_of_layer2(wrapped)
    primary = select_primary_reason(codes)
    assert select_primary_model_type(codes, model_types_of_layer2(wrapped), primary) == "llm"
    assert "alias_merge_llm_fallback" in skipped_steps_of_layer2(wrapped)
    assert "provider private text" not in json.dumps(result)


@pytest.mark.asyncio
async def test_missing_llm_candidate_result_does_not_discard_other_valid_results(run_state):
    state = run_state
    state.rows = [candidate("a"), candidate("b")]
    state.llm.call_structured.return_value = llm_response(0.9)
    result = await state.inspector._run_alias_merge("user")
    assert state.merge.call_args.kwargs["alias_ids"] == ["a"]
    assert state.drop.call_args.args[2] == []
    assert result["business_failure_count"] == 1
    assert result["reason_codes"] == ["RESULT_PARSE_FAILED"]
    assert result["status"] == "partial"


@pytest.mark.asyncio
async def test_layer2_budget_cancels_llm_fallback_before_graph_writes(run_state):
    state = run_state
    state.rows = [candidate()]
    entered, exited = asyncio.Event(), asyncio.Event()

    async def llm_call(*_args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            exited.set()

    state.llm.call_structured.side_effect = llm_call
    result = await state.inspector._run_step("别名归并", "user", state.inspector._run_alias_merge("user"), 0.05)
    assert entered.is_set() and exited.is_set()
    assert result == {"status": "timeout"}
    state.merge.assert_not_awaited()
    state.drop.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_cancellation_is_not_a_normal_fallback_result(run_state):
    state = run_state
    state.rows = [candidate()]
    state.llm.call_structured.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await state.inspector._run_alias_merge("user")
    state.merge.assert_not_awaited()
    state.drop.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelling_bert_requests_never_starts_llm_fallback(run_state):
    state = run_state
    state.rows = [candidate()]
    entered, exited = asyncio.Event(), asyncio.Event()

    async def handler(_request):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            exited.set()

    state.install_transport(handler)
    task = asyncio.create_task(state.inspector._run_alias_merge("user"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()
    assert all(client.is_closed for client in state.http_clients)
    state.llm.call_structured.assert_not_awaited()
    state.merge.assert_not_awaited()
    state.drop.assert_not_awaited()


@pytest.mark.asyncio
async def test_restored_judge_supports_english_template_and_empty_batch():
    llm = SimpleNamespace(call_structured=AsyncMock(return_value=llm_response(0.9)))
    canonical = {"name": "John", "entity_type": "person", "description": "engineer", "aliases": []}
    threshold = inspector_module.AliasMergeConfig().confidence_threshold
    result = await judge_alias_belongs(
        llm, canonical, [candidate(alias_name="Johnny")], threshold=threshold, language="en",
    )
    assert result[0]["decision"] == "merge"
    assert "Canonical Entity" in llm.call_structured.call_args.args[0][0]["content"]
    assert "阿明" in llm.call_structured.call_args.args[0][0]["content"]
    assert await judge_alias_belongs(None, canonical, [], threshold=threshold) == []


@pytest.mark.parametrize("item", [{"alias_index": 1}, {"alias_index": 1, "confidence": True}])
def test_incomplete_or_boolean_llm_confidence_is_rejected(item):
    with pytest.raises(ValidationError):
        AliasJudgeItem.model_validate(item)
