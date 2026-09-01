import asyncio
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import NodeFilter, StorageReadResult
from app.core.memory.storage.outbox.clients import (
    ProjectionClients, project_event,
)
from app.core.memory.storage.outbox.consumer import _consume_claim, cleanup_outbox_events, consume_outbox_batch
from app.core.memory.storage.outbox.exceptions import (
    ClaimLostError, OutboxEnqueueError, safe_error,
)
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.types import ClaimedEvent, OutboxEventInput
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient


def event():
    return ClaimedEvent(uuid4(), 1, "Statement", "node-1", "upsert", 0, uuid4())


def read_result(items):
    return StorageReadResult.from_items(items)


def repository():
    repo = Mock(processing_timeout=300, error_max_length=64)
    repo.heartbeat = AsyncMock(return_value=True)
    repo.begin_attempt = AsyncMock(side_effect=[1, 2, 3, None])
    repo.mark_processed = AsyncMock(return_value=True)
    repo.mark_failed = AsyncMock(return_value=True)
    repo.mark_expired_failed = AsyncMock(return_value=0)
    return repo


@pytest.mark.parametrize("kwargs", [
    {"label": "InjectedLabel"}, {"node_id": "  "}, {"node_id": 123},
    {"operation": "replay"}, {"payload": {"secret": "no"}},
])
def test_input_rejects_invalid_fields(kwargs):
    with pytest.raises(ValidationError):
        OutboxEventInput(**({"label": MemoryNodeType.STATEMENT, "node_id": "n"} | kwargs))


def test_input_immutable_and_business_id_unchanged():
    value = OutboxEventInput(label=MemoryNodeType.STATEMENT, node_id=" n ")
    assert value.node_id == " n "
    with pytest.raises(ValidationError):
        value.node_id = "different"


async def test_producer_empty_and_failure_contract():
    repo = Mock(enqueue_many=AsyncMock(side_effect=RuntimeError("secret SQL document password")))
    assert await enqueue_events([], repository=repo) == []
    repo.enqueue_many.assert_not_awaited()
    value = OutboxEventInput(label=MemoryNodeType.STATEMENT, node_id="n")
    with pytest.raises(OutboxEnqueueError) as caught:
        await enqueue_events([value], repository=repo)
    assert caught.value.primary_committed is True
    assert caught.value.event_ids == (value.id,)
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


async def test_default_repository_reuses_project_sync_sessionmaker():
    from app.db import SessionLocal
    from app.core.memory.storage.outbox.repository import create_repository

    assert create_repository().sessions is SessionLocal


@pytest.mark.parametrize("failures", [0, 1, 2, 3])
async def test_three_total_attempts_with_same_token(failures):
    repo, item = repository(), event()
    project = AsyncMock(side_effect=[TimeoutError("secret")] * failures + [None])
    assert await _consume_claim(item, repo, project) == ("failed" if failures == 3 else "processed")
    assert project.await_count == min(failures + 1, 3)
    assert all(call.args == (item.id, item.claim_token) for call in repo.begin_attempt.await_args_list)
    assert repo.mark_failed.await_count == (failures == 3)
    if failures == 3:
        assert repo.mark_failed.await_args.args[2] == "TimeoutError"


async def test_all_projection_failures_use_all_attempts():
    repo = repository()
    project = AsyncMock(side_effect=ValueError("do not log payload"))
    assert await _consume_claim(event(), repo, project) == "failed"
    assert project.await_count == 3


async def test_ack_failure_never_repeats_es():
    repo = repository()
    repo.mark_processed.side_effect = ConnectionError("PG unavailable")
    project = AsyncMock()
    with pytest.raises(ConnectionError):
        await _consume_claim(event(), repo, project)
    assert project.await_count == 1
    repo.mark_failed.assert_not_awaited()


async def test_invalid_claim_cannot_send_projection():
    repo = repository()
    repo.heartbeat.return_value = False
    project = AsyncMock()
    assert await _consume_claim(event(), repo, project) == "lost"
    project.assert_not_awaited()
    repo.mark_processed.assert_not_awaited()
    repo.mark_failed.assert_not_awaited()


async def test_heartbeat_loss_cancels_inflight_attempt():
    repo = repository()
    repo.processing_timeout = 0.12  # Accelerate the heartbeat for this fake repo.
    repo.heartbeat.side_effect = [True, False]
    cancelled = asyncio.Event()

    async def project(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    assert await _consume_claim(event(), repo, project) == "lost"
    assert cancelled.is_set()
    assert repo.begin_attempt.await_count == 1


async def test_projection_timeout_is_bounded_and_failed():
    repo = repository()
    repo.processing_timeout = 0.02

    async def slow(*args):
        await asyncio.Event().wait()

    assert await _consume_claim(event(), repo, slow) == "failed"
    assert repo.begin_attempt.await_count == 3


async def test_empty_scan_expires_leases_without_connecting_clients(monkeypatch):
    repo = repository()
    repo.claim_batch = AsyncMock(return_value=[])
    create = AsyncMock(side_effect=AssertionError("no client needed"))
    monkeypatch.setattr(ProjectionClients, "project", create)
    stats = await consume_outbox_batch(100, "worker", repository=repo)
    assert stats["claimed"] == 0
    repo.mark_expired_failed.assert_awaited_once_with(100)
    repo.claim_batch.assert_awaited_once_with("worker", 1)
    create.assert_not_awaited()


async def test_batch_claims_only_immediate_capacity():
    repo = repository()
    repo.claim_batch = AsyncMock(side_effect=[[event()], [event()], []])
    repo.begin_attempt.side_effect = [1, 1]
    result = await consume_outbox_batch(100, "worker", repository=repo, projector=AsyncMock())
    assert result["processed"] == 2
    assert all(call.args == ("worker", 1) for call in repo.claim_batch.await_args_list)


async def test_cleanup_small_batches_and_bound():
    repo = Mock(cleanup=AsyncMock(side_effect=[{"processed": 2, "failed": 0}, {"processed": 1, "failed": 1}]))
    assert await cleanup_outbox_events(2, repository=repo) == {"processed": 3, "failed": 1}
    repo.cleanup = AsyncMock(return_value={"processed": 2, "failed": 2})
    await cleanup_outbox_events(2, repository=repo)
    assert repo.cleanup.await_count == 100


@pytest.mark.parametrize("operation", ["upsert", "draft_delete"])
async def test_current_source_wins_for_upsert_and_draft_delete(operation):
    item = replace(event(), operation=operation)
    source = {"id": item.node_id, "text": "current", "embedding": [1.0, 2.0],
              "delete_at": datetime(2026, 1, 1)}
    neo = Mock(get_node=AsyncMock(return_value=read_result([source])))
    es = Mock(save_node=AsyncMock(), delete_node=AsyncMock())
    check = AsyncMock()
    await project_event(item, neo, es, check_claim=check)
    document = es.save_node.await_args.args[1]
    assert document == source
    assert "projection" not in neo.get_node.await_args.kwargs
    es.delete_node.assert_not_awaited()
    check.assert_awaited_once()


async def test_delete_event_removes_document_without_rereading_source():
    item = replace(event(), operation="delete")
    neo = Mock(get_node=AsyncMock())
    es = Mock(save_node=AsyncMock(), delete_node=AsyncMock())
    check = AsyncMock()
    await project_event(item, neo, es, check_claim=check)
    neo.get_node.assert_not_awaited()
    es.save_node.assert_not_awaited()
    assert es.delete_node.await_args.kwargs == {"draft": False}
    assert es.delete_node.await_args.args[1] == NodeFilter.eq("id", item.node_id)
    check.assert_awaited_once()


async def test_delete_event_skips_es_when_lease_is_lost():
    item = replace(event(), operation="delete")
    es = Mock(save_node=AsyncMock(), delete_node=AsyncMock())
    with pytest.raises(ClaimLostError):
        await project_event(
            item,
            Mock(get_node=AsyncMock()),
            es,
            check_claim=AsyncMock(side_effect=ClaimLostError()),
        )
    es.delete_node.assert_not_awaited()


async def test_missing_source_physically_deletes():
    neo = Mock(get_node=AsyncMock(return_value=read_result([])))
    es = Mock(save_node=AsyncMock(), delete_node=AsyncMock())
    await project_event(event(), neo, es, check_claim=AsyncMock())
    assert es.delete_node.await_args.kwargs == {"draft": False}
    es.save_node.assert_not_awaited()


@pytest.mark.parametrize(
    "result",
    [
        None,
        [{"id": "node-1"}],
        read_result([{"id": "wrong"}]),
        read_result([{"id": "node-1"}] * 2),
        StorageReadResult.model_construct(
            backend=None,
            items=[None],
            total=1,
        ),
        StorageReadResult(items=[], total=1),
    ],
)
async def test_incomplete_or_ambiguous_read_never_deletes(result):
    neo = Mock(get_node=AsyncMock(return_value=result))
    es = Mock(save_node=AsyncMock(), delete_node=AsyncMock())
    with pytest.raises(ValueError):
        await project_event(event(), neo, es, check_claim=AsyncMock())
    es.save_node.assert_not_awaited()
    es.delete_node.assert_not_awaited()


async def test_read_failure_and_lease_loss_never_write_es():
    neo = Mock(get_node=AsyncMock(side_effect=TimeoutError()))
    es = Mock(save_node=AsyncMock(), delete_node=AsyncMock())
    with pytest.raises(TimeoutError):
        await project_event(event(), neo, es, check_claim=AsyncMock())
    neo.get_node.side_effect = None
    neo.get_node.return_value = read_result([])
    with pytest.raises(ClaimLostError):
        await project_event(event(), neo, es, check_claim=AsyncMock(side_effect=ClaimLostError()))
    es.save_node.assert_not_awaited()
    es.delete_node.assert_not_awaited()


def test_diagnostics_do_not_leak_error_details():
    assert safe_error(ValueError("password=secret"), 5) == "Value"


async def test_each_retry_rereads_source_instead_of_reusing_document():
    repo, item = repository(), event()
    neo = Mock(get_node=AsyncMock(side_effect=[
        read_result([{"id": item.node_id, "text": "old"}]),
        read_result([{"id": item.node_id, "text": "new"}]),
    ]))
    es = Mock(save_node=AsyncMock(side_effect=[TimeoutError(), None]))

    async def project(claim, check_claim):
        await project_event(claim, neo, es, check_claim=check_claim)

    assert await _consume_claim(item, repo, project) == "processed"
    assert [call.args[1]["text"] for call in es.save_node.await_args_list] == ["old", "new"]


async def test_client_cleanup_closes_both_even_on_error():
    clients = ProjectionClients(30)
    clients.neo4j = Mock(close=AsyncMock(side_effect=RuntimeError()))
    clients.elastic = Mock(close=AsyncMock())
    clients.redis = Mock(aclose=AsyncMock())
    async with clients:
        pass
    clients.neo4j.close.assert_awaited_once()
    clients.elastic.close.assert_awaited_once()
    clients.redis.aclose.assert_awaited_once()


async def test_clients_reuse_index_initialization_and_own_redis_pool(monkeypatch):
    clients = ProjectionClients(30)
    clients.neo4j = Mock(
        get_node=AsyncMock(return_value=read_result([])),
        close=AsyncMock(),
    )
    clients.elastic = Mock(delete_node=AsyncMock(), close=AsyncMock())
    ensure = AsyncMock()
    redis = Mock(return_value=Mock(aclose=AsyncMock()))
    monkeypatch.setattr("app.core.memory.storage.outbox.clients.ensure_indices", ensure)
    monkeypatch.setattr("app.core.memory.storage.outbox.clients.Redis", redis)
    async with clients:
        await clients.project(event(), AsyncMock())
        await clients.project(event(), AsyncMock())
    ensure.assert_awaited_once()
    redis.assert_called_once()
    redis.return_value.aclose.assert_awaited_once()


async def test_clients_use_provider_elastic_client_without_sdk_retries(monkeypatch):
    clients = ProjectionClients(7)
    clients.neo4j = Mock(
        get_node=AsyncMock(return_value=read_result([{
            "id": "node-1",
            "delete_at": datetime(2026, 1, 1),
        }])),
        close=AsyncMock(),
    )
    transport = Mock(
        index=AsyncMock(return_value={"result": "created"}),
        close=AsyncMock(),
    )
    constructor = Mock(return_value=transport)
    ensure = AsyncMock()
    redis = Mock(return_value=Mock(aclose=AsyncMock()))
    monkeypatch.setattr(
        "app.core.memory.storage.outbox.clients.build_elasticsearch_client_config",
        lambda: {
            "hosts": ["http://localhost:9200"],
            "request_timeout": 60,
            "max_retries": 10,
            "retry_on_timeout": True,
        },
    )
    monkeypatch.setattr(
        "app.core.memory.storage.outbox.clients.AsyncElasticsearch",
        constructor,
    )
    monkeypatch.setattr(
        "app.core.memory.storage.outbox.clients.ensure_indices",
        ensure,
    )
    monkeypatch.setattr(
        "app.core.memory.storage.outbox.clients.Redis",
        redis,
    )

    async with clients:
        await clients.project(event(), AsyncMock())

    assert isinstance(clients.elastic, ElasticClient)
    assert constructor.call_args.kwargs["request_timeout"] == 7
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert constructor.call_args.kwargs["retry_on_timeout"] is False
    assert transport.index.await_args.kwargs["document"] == {
        "id": "node-1",
        "delete_at": "2026-01-01T00:00:00Z",
    }
