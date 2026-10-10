"""Tests for P3 recall orchestration and exact Elasticsearch bucket reads."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.core.memory.storage.custom.preference_recall import (
    PreferenceRecallCandidate,
    PreferenceRecallStorage,
)
from app.core.memory.storage.enums import BackendType
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage_services.preference_engine.models import (
    PreferenceRecallRequest,
    PreferenceRecallTarget,
)
from app.core.memory.storage_services.preference_engine.recall_service import (
    PreferenceRecallLimits,
    PreferenceRecallService,
    execute_preference_recall,
)
from app.core.memory.storage_services.preference_engine import recall_service


class FakeRunner:
    def __init__(self, targets):
        self.targets = targets
        self.calls = []

    async def route_recall(self, **kwargs):
        self.calls.append(kwargs)
        return self.targets


class FakeStorage:
    def __init__(self, candidates=()):
        self.candidates = list(candidates)
        self.calls = []

    async def query_buckets(self, **kwargs):
        self.calls.append(kwargs)
        return self.candidates


class FakeRawElasticsearch:
    def __init__(self):
        self.searches = None

    async def msearch(self, *, searches, max_concurrent_searches):
        self.searches = searches
        return {
            "responses": [
                {
                    "_shards": {"failed": 0},
                    "hits": {
                        "hits": [
                            {
                                "_source": {
                                    "preference_text": ["use pytest"],
                                    "updated_at": "2026-09-01T01:00:00Z",
                                }
                            }
                        ]
                    },
                },
                {"_shards": {"failed": 0}, "hits": {"hits": []}},
            ]
        }


def _service(runner, storage, **limit_overrides):
    limits = {
        "max_context_messages": 2,
        "max_context_chars": 20,
        "max_preferences": 10,
        "per_bucket_limit": 3,
        "timeout_ms": 1000,
    }
    limits.update(limit_overrides)
    return PreferenceRecallService(
        configured_workspace_id="workspace-1",
        prompt_runner=runner,
        storage=storage,
        limits=PreferenceRecallLimits(**limits),
    )


def _request(message, context_messages=None):
    return PreferenceRecallRequest.model_validate(
        {
            "current_user_message": message,
            "context_messages": context_messages or [],
        }
    )


@pytest.mark.asyncio
async def test_empty_targets_are_successful_without_storage_access(caplog):
    runner = FakeRunner([])
    storage = FakeStorage()

    with caplog.at_level("INFO", logger=recall_service.__name__):
        result = await _service(runner, storage).recall(
            workspace_id="workspace-1",
            end_user_id="user-1",
            request=_request("聊聊天"),
        )

    assert result == []
    assert storage.calls == []
    assert "[PreferenceRecall][2/3] step2 completed" in caplog.text
    assert "[PreferenceRecall][3/3] step3 skipped" in caplog.text
    assert "reason=no_targets" in caplog.text


@pytest.mark.asyncio
async def test_context_keeps_recent_messages_within_character_budget():
    runner = FakeRunner([])

    await _service(runner, FakeStorage(), max_context_chars=7).recall(
        workspace_id="workspace-1",
        end_user_id="user-1",
        request=_request(
            "继续",
            [
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "abcdef"},
                {"role": "user", "content": "XYZ"},
            ],
        ),
    )

    assert runner.calls[0]["context_messages"] == [
        {"role": "assistant", "content": "cdef"},
        {"role": "user", "content": "XYZ"},
    ]


@pytest.mark.asyncio
async def test_workspace_mismatch_stops_before_model_and_storage(caplog):
    runner = FakeRunner([])
    storage = FakeStorage()

    with caplog.at_level("INFO", logger=recall_service.__name__):
        with pytest.raises(recall_service.PreferenceRecallForbiddenError):
            await _service(runner, storage).recall(
                workspace_id="other-workspace",
                end_user_id="user-1",
                request=_request("继续"),
            )

    assert runner.calls == []
    assert storage.calls == []
    assert "[PreferenceRecall][1/3] step1 failed" in caplog.text
    assert "reason=workspace_mismatch" in caplog.text


@pytest.mark.asyncio
async def test_specific_bucket_wins_dedup_when_global_is_newer():
    target = PreferenceRecallTarget(
        subject="tool_choice",
        situation_key="code_generation",
    )
    now = datetime.now(timezone.utc)
    storage = FakeStorage(
        [
            PreferenceRecallCandidate(
                preference_text="使用  pytest",
                updated_at=now,
                target_index=0,
                is_global=True,
                item_index=0,
            ),
            PreferenceRecallCandidate(
                preference_text=" 使用 pytest ",
                updated_at=now - timedelta(days=1),
                target_index=0,
                is_global=False,
                item_index=0,
            ),
        ]
    )

    result = await _service(FakeRunner([target]), storage).recall(
        workspace_id="workspace-1",
        end_user_id="user-1",
        request=_request("写测试"),
    )

    assert [item.preference_text for item in result] == ["使用 pytest"]
    assert result[0].updated_at == now - timedelta(days=1)
    assert storage.calls[0]["per_bucket_limit"] == 3


@pytest.mark.asyncio
async def test_final_result_limit_is_independent_of_per_bucket_query_limit():
    now = datetime.now(timezone.utc)
    storage = FakeStorage(
        [
            PreferenceRecallCandidate(
                preference_text=f"preference {index}",
                updated_at=now - timedelta(seconds=index),
                target_index=index // 3,
                is_global=True,
                item_index=index % 3,
            )
            for index in range(12)
        ]
    )

    result = await _service(
        FakeRunner(
            [PreferenceRecallTarget(subject="tool_choice", situation_key="global")]
            * 4
        ),
        storage,
    ).recall(
        workspace_id="workspace-1",
        end_user_id="user-1",
        request=_request("写测试"),
    )

    assert storage.calls[0]["per_bucket_limit"] == 3
    assert len(result) == 10


@pytest.mark.asyncio
async def test_shared_adapter_rejects_invalid_role_before_database_access(monkeypatch):
    def fail_if_database_opened():
        raise AssertionError("database must not be opened")

    monkeypatch.setattr(
        "app.core.memory.storage_services.preference_engine.recall_service.get_async_db_context",
        fail_if_database_opened,
    )

    with pytest.raises(recall_service.PreferenceRecallValidationError):
        await execute_preference_recall(
            config_id=uuid.uuid4(),
            workspace_id="workspace-1",
            end_user_id="user-1",
            request_payload={
                "current_user_message": "继续",
                "context_messages": [{"role": "tool", "content": "output"}],
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_route", [False, True])
async def test_shared_adapter_closes_injected_model_client(
    monkeypatch, fail_route
):
    config_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    closed = False

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    class FakeMemoryConfigService:
        def __init__(self, db):
            pass

        async def load_memory_config_async(self, requested_config_id):
            assert requested_config_id == config_id
            return SimpleNamespace(
                workspace_id=workspace_id,
                llm_model_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
            )

    class FakeModelClient:
        async def aclose(self):
            nonlocal closed
            closed = True

    async def fake_get_llm_client(db, model_id, tenant_id):
        return FakeModelClient()

    class FailingRunner:
        async def route_recall(self, **kwargs):
            raise RuntimeError("provider failed")

    class FakeBackendFactory:
        def get_client(self, backend_type):
            assert backend_type == BackendType.ELASTIC
            return ElasticClient()

    monkeypatch.setattr(recall_service, "get_async_db_context", fake_db_context)
    monkeypatch.setattr(recall_service, "MemoryConfigService", FakeMemoryConfigService)
    monkeypatch.setattr(
        recall_service.ModelClientMixin,
        "get_llm_client_async",
        fake_get_llm_client,
    )
    monkeypatch.setattr(
        recall_service,
        "PreferencePromptRunner",
        lambda *_: FailingRunner() if fail_route else FakeRunner([]),
    )
    monkeypatch.setattr(recall_service, "load_preference_ontology", lambda *_: object())
    monkeypatch.setattr(
        recall_service,
        "get_storage_service",
        lambda: SimpleNamespace(_backend_factory=FakeBackendFactory()),
    )

    async def run_recall():
        return await execute_preference_recall(
            config_id=config_id,
            workspace_id=workspace_id,
            end_user_id="user-1",
            request_payload={"current_user_message": "继续", "context_messages": []},
        )

    if fail_route:
        with pytest.raises(RuntimeError, match="preference recall dependency failed"):
            await run_recall()
    else:
        assert await run_recall() == []
    assert closed is True


@pytest.mark.asyncio
async def test_route_failure_is_logged_once_without_request_body(caplog):
    class FailingRunner:
        async def route_recall(self, **kwargs):
            raise RuntimeError("sensitive request body")

    with caplog.at_level("INFO", logger=recall_service.__name__):
        with pytest.raises(RuntimeError, match="preference recall dependency failed"):
            await _service(FailingRunner(), FakeStorage()).recall(
                workspace_id="workspace-1",
                end_user_id="user-1",
                request=_request("sensitive request body"),
            )

    failures = [record for record in caplog.records if " step2 failed " in record.message]
    assert len(failures) == 1
    assert "reason=RuntimeError" in failures[0].message
    assert "sensitive request body" not in caplog.text


@pytest.mark.asyncio
async def test_query_uses_exact_active_user_and_bucket_filters_only():
    raw = FakeRawElasticsearch()
    elastic = ElasticClient()
    elastic.client = raw
    storage = PreferenceRecallStorage.__new__(PreferenceRecallStorage)
    storage._client = elastic

    result = await storage.query_buckets(
        end_user_id="user-1",
        targets=[
            PreferenceRecallTarget(
                subject="tool_choice",
                situation_key="code_generation",
            )
        ],
        per_bucket_limit=3,
    )

    assert [item.preference_text for item in result] == ["use pytest"]
    assert raw.searches[1]["size"] == 3
    assert raw.searches[3]["size"] == 3
    filters = raw.searches[1]["query"]["bool"]["filter"]
    assert filters == [
        {"term": {"end_user_id": "user-1"}},
        {"term": {"domain": "coding"}},
        {"term": {"subject": "tool_choice"}},
        {"term": {"situation_key": "code_generation"}},
        {"term": {"status": "active"}},
    ]
    assert "knn" not in raw.searches[1]
    assert "must" not in raw.searches[1]["query"]["bool"]
