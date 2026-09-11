"""Opt-in tests: use ONLY an explicitly supplied disposable PostgreSQL database.

RUN_OUTBOX_POSTGRES_TESTS=1 OUTBOX_TEST_DATABASE_URL=postgresql+asyncpg://... pytest ...
Each test creates and drops a uniquely named schema; never uses application DB settings.
"""

import asyncio
import importlib.util
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.memory.storage.outbox.consumer import consume_outbox_batch
from app.core.memory.storage.outbox.exceptions import OutboxConflictError
from app.core.memory.storage.outbox.repository import OutboxRepository, db_now, events
from app.core.memory.storage.outbox.types import OutboxEventInput

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OUTBOX_POSTGRES_TESTS") != "1" or not os.getenv("OUTBOX_TEST_DATABASE_URL"),
    reason="requires explicitly configured disposable PostgreSQL",
)


@pytest_asyncio.fixture
async def pg():
    url = os.environ["OUTBOX_TEST_DATABASE_URL"]
    schema = "outbox_test_" + uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as connection:
        await connection.execute(sa.schema.CreateSchema(schema))
    engine = create_async_engine(url, poolclass=NullPool,
                                 connect_args={"server_settings": {"search_path": schema, "statement_timeout": "10000"}})
    sync_url = sa.engine.make_url(url).set(drivername="postgresql+psycopg2")
    sync_engine = create_engine(
        sync_url,
        poolclass=NullPool,
        connect_args={"options": f"-c search_path={schema} -c timezone=UTC -c statement_timeout=10000"},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(events.create)
        yield OutboxRepository(
            sessionmaker(sync_engine, expire_on_commit=False)
        ), engine, schema
    finally:
        await asyncio.to_thread(sync_engine.dispose)
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(sa.schema.DropSchema(schema, cascade=True))
        await admin.dispose()


def item(node_id="node", **kwargs):
    return OutboxEventInput(label="Statement", node_id=node_id, **kwargs)


async def rows(engine):
    async with engine.connect() as connection:
        return (await connection.execute(sa.select(events).order_by(events.c.sequence))).mappings().all()


async def test_idempotent_enqueue_conflict_rolls_back_entire_batch(pg):
    repo, engine, _ = pg
    first = item()
    assert await repo.enqueue_many([first, first]) == [first.id, first.id]
    assert await repo.enqueue_many([first]) == [first.id]
    assert len(await rows(engine)) == 1
    with pytest.raises(OutboxConflictError):
        await repo.enqueue_many([item("second"), item("different", id=first.id)])
    assert len(await rows(engine)) == 1
    # A conflict later than the first insert chunk still rolls back all 1000 rows.
    from uuid import UUID
    last = item("last", id=UUID(int=(1 << 128) - 1))
    await repo.enqueue_many([last])
    with pytest.raises(OutboxConflictError):
        await repo.enqueue_many([item(str(i), id=UUID(int=i + 1)) for i in range(1000)]
                                + [item("conflict", id=last.id)])
    assert len(await rows(engine)) == 2


async def test_concurrent_idempotent_enqueue(pg):
    repo, engine, _ = pg
    inputs = [item(str(i)) for i in range(20)]
    await asyncio.gather(*(repo.enqueue_many(inputs if i % 2 else inputs[::-1]) for i in range(4)))
    assert len(await rows(engine)) == 20


async def test_same_node_serial_other_nodes_parallel(pg):
    repo, engine, _ = pg
    first, second, different = item(), item(), item("different")
    for value in (first, second, different):
        await repo.enqueue_many([value])
    batches = await asyncio.gather(*(repo.claim_batch(f"worker-{i}", 10) for i in range(6)))
    claims = [claim for batch in batches for claim in batch]
    assert {claim.id for claim in claims} == {first.id, different.id}
    assert len(claims) == 2
    old = next(claim for claim in claims if claim.id == first.id)
    assert await repo.begin_attempt(old.id, old.claim_token) == 1
    assert await repo.mark_failed(old.id, old.claim_token, "TimeoutError")
    assert (await repo.claim_batch("next", 1))[0].id == second.id
    await repo.enqueue_many([first])  # Idempotent enqueue must NOT resurrect a failure.
    assert (await rows(engine))[0]["status"] == "failed"


async def test_locked_head_skips_whole_node_not_just_row(pg):
    repo, engine, _ = pg
    first, second, different = item(), item(), item("different")
    for value in (first, second, different):
        await repo.enqueue_many([value])
    with repo.sessions.begin() as session:
        session.execute(sa.select(events).where(events.c.id == first.id).with_for_update())
        claims = await asyncio.wait_for(repo.claim_batch("other-worker", 10), 3)
        assert [claim.id for claim in claims] == [different.id]
    assert (await repo.claim_batch("after-unlock", 1))[0].id == first.id


async def test_token_guards_attempt_bound_and_terminal_states(pg):
    repo, engine, _ = pg
    await repo.enqueue_many([item()])
    claim = (await repo.claim_batch("worker", 1))[0]
    wrong = uuid4()
    assert not await repo.heartbeat(claim.id, wrong)
    assert await repo.begin_attempt(claim.id, wrong) is None
    assert not await repo.mark_processed(claim.id, wrong)
    assert not await repo.mark_failed(claim.id, wrong, "wrong")
    assert [await repo.begin_attempt(claim.id, claim.claim_token) for _ in range(4)] == [1, 2, 3, None]
    assert await repo.mark_failed(claim.id, claim.claim_token, "x" * 5000)
    assert not await repo.mark_processed(claim.id, claim.claim_token)
    assert not await repo.heartbeat(claim.id, claim.claim_token)
    assert await repo.claim_batch("next", 1) == []
    row = (await rows(engine))[0]
    assert row["attempt_count"] == 3 and len(row["last_error"]) == 4096
    assert row["claim_token"] is None and row["failed_at"] is not None


async def test_expired_claim_never_revives_and_unblocks_next(pg):
    repo, engine, _ = pg
    first, second = item(), item()
    await repo.enqueue_many([first])
    await repo.enqueue_many([second])
    claim = (await repo.claim_batch("dead", 1))[0]
    await repo.begin_attempt(claim.id, claim.claim_token)
    async with engine.begin() as connection:
        await connection.execute(sa.update(events).where(events.c.id == claim.id).values(
            heartbeat_at=db_now() - timedelta(seconds=301)))
    assert not await repo.heartbeat(claim.id, claim.claim_token)
    assert await repo.begin_attempt(claim.id, claim.claim_token) is None
    assert not await repo.mark_processed(claim.id, claim.claim_token)
    counts = await asyncio.gather(repo.mark_expired_failed(10), repo.mark_expired_failed(10))
    assert sum(counts) == 1
    row = (await rows(engine))[0]
    assert row["status"] == "failed" and row["attempt_count"] == 1
    assert row["claim_token"] is None
    assert (await repo.claim_batch("next", 1))[0].id == second.id


async def test_cleanup_uses_terminal_times_never_active_rows(pg):
    repo, engine, _ = pg
    values = [item(str(i)) for i in range(6)]
    for value in values:
        await repo.enqueue_many([value])
    claims = await repo.claim_batch("worker", 5)
    for claim in claims[:2]:
        await repo.mark_processed(claim.id, claim.claim_token)
    for claim in claims[2:4]:
        await repo.mark_failed(claim.id, claim.claim_token, "test")
    async with engine.begin() as connection:
        await connection.execute(sa.update(events).values(created_at=db_now() - timedelta(days=100)))
        await connection.execute(sa.update(events).where(events.c.id == claims[0].id).values(
            processed_at=db_now() - timedelta(days=31)))
        await connection.execute(sa.update(events).where(events.c.id == claims[2].id).values(
            failed_at=db_now() - timedelta(days=61)))
    assert await repo.cleanup(1) == {"processed": 1, "failed": 1}
    remaining = await rows(engine)
    assert {row["status"] for row in remaining} == {"pending", "processing", "processed", "failed"}
    assert len(remaining) == 4


async def test_full_consumer_three_attempts_then_next_node_event(pg):
    repo, engine, _ = pg
    first, second = item(), item()
    await repo.enqueue_many([first])
    await repo.enqueue_many([second])
    calls = []

    async def project(claim, check_claim):
        await check_claim()
        calls.append(claim.id)
        if claim.id == first.id:
            raise TimeoutError("private response content")

    stats = await consume_outbox_batch(10, "worker", repository=repo, projector=project)
    assert calls == [first.id] * 3 + [second.id]
    assert stats["failed"] == 1 and stats["processed"] == 1
    stored = await rows(engine)
    assert [row["attempt_count"] for row in stored] == [3, 1]
    assert stored[0]["last_error"] == "TimeoutError"
    assert (await consume_outbox_batch(10, "redelivery", repository=repo, projector=project))["claimed"] == 0


@pytest.mark.parametrize("edition", ["core", "enterprise"])
async def test_existing_migration_upgrade_matches_model_and_downgrades(pg, edition):
    _, engine, schema = pg
    api = Path(__file__).resolve().parents[5]
    if edition == "core":
        path = api / "migrations/versions/7a6d8f2c9b14_202608261200_memory_storage_outbox.py"
    else:
        path = api.parent.parent / "migrations/versions/8b7e9d3f0a25_202608261200_memory_storage_outbox.py"
    if not path.exists():
        pytest.skip(f"{edition} migration is not present in this checkout")
    spec = importlib.util.spec_from_file_location("outbox_test_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def verify(connection):
        before = sa.inspect(connection)
        columns = before.get_columns(events.name, schema=schema)
        indexes = before.get_indexes(events.name, schema=schema)
        constraints = before.get_check_constraints(events.name, schema=schema)
        events.drop(connection)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        after = sa.inspect(connection)
        assert [(c["name"], str(c["type"]), c["nullable"], c.get("default")) for c in after.get_columns(events.name, schema=schema)] == [
            (c["name"], str(c["type"]), c["nullable"], c.get("default")) for c in columns]
        assert after.get_indexes(events.name, schema=schema) == indexes
        assert after.get_check_constraints(events.name, schema=schema) == constraints
        migration.downgrade()
        assert not sa.inspect(connection).has_table(events.name, schema=schema)

    async with engine.begin() as connection:
        await connection.run_sync(verify)
