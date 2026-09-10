import inspect
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import GraphWriteResult, MemoryGraphWriteCommand
from app.core.memory.storage.provider.neo4j import graph_write_queries, graph_writer
from app.core.memory.storage.provider.neo4j.client import Neo4jClient


class AsyncRecords:
    def __init__(self, records):
        self.records = records

    def __aiter__(self):
        async def iterate():
            for record in self.records:
                yield record

        return iterate()


class Transaction:
    def __init__(self, records_by_call):
        self.records_by_call = list(records_by_call)
        self.calls = []

    async def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return AsyncRecords(self.records_by_call.pop(0))


class Session:
    def __init__(self, transaction, query_records=None):
        self.transaction = transaction
        self.query_records = query_records or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def run(self, query, **parameters):
        result = Mock()
        result.data = AsyncMock(return_value=self.query_records)
        return result

    async def execute_write(self, callback):
        return await callback(self.transaction)


class Driver:
    def __init__(self, records_by_call, query_records=None):
        self.transaction = Transaction(records_by_call)
        self.query_records = query_records

    def session(self):
        return Session(self.transaction, self.query_records)


def test_graph_writer_does_not_depend_on_legacy_neo4j_repository():
    assert "app.repositories.neo4j" not in inspect.getsource(graph_writer)


def test_statement_writer_does_not_replace_missing_dates_with_blank_strings():
    query = graph_write_queries.STATEMENT_NODE_SAVE

    assert "valid_at: statement.valid_at" in query
    assert "invalid_at: statement.invalid_at" in query
    assert 'coalesce(statement.valid_at, "")' not in query
    assert 'coalesce(statement.invalid_at, "")' not in query


def empty_graph(**overrides):
    values = {
        "dialogue_nodes": [],
        "chunk_nodes": [],
        "statement_nodes": [],
        "entity_nodes": [],
        "perceptual_nodes": [],
        "entity_edges": [],
        "statement_chunk_edges": [],
        "statement_entity_edges": [],
        "perceptual_edges": [],
    }
    values.update(overrides)
    return MemoryGraphWriteCommand(**values)


async def test_neo4j_client_exposes_graph_writer_entry_points(monkeypatch):
    client = Neo4jClient()
    driver = Mock(name="driver")
    client.client = driver
    expected = GraphWriteResult()
    save_graph = AsyncMock(return_value=expected)
    save_summaries = AsyncMock(return_value=expected)
    monkeypatch.setattr(graph_writer, "save_memory_graph", save_graph)
    monkeypatch.setattr(graph_writer, "save_memory_summaries", save_summaries)
    command = empty_graph()
    summaries = [Mock()]

    assert await client.save_memory_graph(command) is expected
    assert await client.save_memory_summaries(summaries) is expected

    save_graph.assert_awaited_once_with(driver, command)
    save_summaries.assert_awaited_once_with(driver, summaries)


async def test_graph_writer_returns_committed_node_ids_by_label():
    dialogue = Mock()
    dialogue.model_dump.return_value = {"id": "dialog-1"}
    driver = Driver([[{"uuid": "dialog-1"}]])

    result = await graph_writer.save_memory_graph(
        driver,
        empty_graph(dialogue_nodes=[dialogue]),
    )

    assert result.node_ids == {MemoryNodeType.DIALOGUE: ["dialog-1"]}
    assert result.relationship_count == 0
    assert len(driver.transaction.calls) == 1


async def test_summary_writer_commits_nodes_and_edges_in_one_transaction():
    summary = Mock(
        id="summary-1",
        chunk_ids=["chunk-1"],
        end_user_id="user-1",
        run_id="run-1",
        created_at="2026-09-01T00:00:00Z",
    )
    summary.model_dump.return_value = {"id": "summary-1"}
    driver = Driver(
        [
            [{"uuid": "summary-1"}],
            [{"uuid": "relationship-1"}],
        ]
    )

    result = await graph_writer.save_memory_summaries(driver, [summary])

    assert result.node_ids == {
        MemoryNodeType.MEMORY_SUMMARY: ["summary-1"]
    }
    assert result.relationship_count == 1
    assert len(driver.transaction.calls) == 2


async def test_graph_writer_propagates_transaction_failure():
    class FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def execute_write(self, callback):
            raise RuntimeError("neo4j unavailable")

    class FailingDriver:
        def session(self):
            return FailingSession()

    with pytest.raises(RuntimeError, match="neo4j unavailable"):
        await graph_writer.save_memory_graph(FailingDriver(), empty_graph())
