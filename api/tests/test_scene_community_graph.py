import asyncio
import copy
from datetime import datetime, timezone
from contextvars import ContextVar
from functools import wraps
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.services.scene_community_graph_access import AccessResolver
from app.services.scene_community_graph_assembler import assemble
from app.cache.memory.scene_community_graph_cache import RedisDataCache, encode_data
from app.services.scene_community_graph_types import AuthorizedUser, Options
from app.repositories.neo4j.scene_community_graph_repository import Neo4jRepository, COMMUNITIES_QUERY, SCENES_QUERY
from app.schemas.scene_community_graph_schema import CommunityProperties, GraphData
from app.services.scene_community_graph_service import SceneGraphService
from app.core.exceptions import BusinessException
from app.core.error_codes import HTTP_MAPPING

WORKSPACE = UUID("10000000-0000-4000-8000-000000000001")
USER = UUID("20000000-0000-4000-8000-000000000001")
STAMP = "2026-10-23T00:00:00Z"
STAMP_MS = 1792713600000


def community(identifier="c1", **props):
    return {"id": identifier, "properties": {"topic_name": "Project", "updated_at": STAMP, **props}}


def scene(identifier="s1", parent="c1", **props):
    return {"id": identifier, "properties": {"content": "Summary", "scene_community_id": parent, "ended_at": STAMP, **props}}


class Lookup:
    def __init__(self):
        self.flags = {(WORKSPACE, USER): True}
        self.targets = {}
        self.calls = 0

    async def find_active_flag(self, workspace_id, user_id):
        self.calls += 1
        return self.flags.get((workspace_id, user_id))

    async def merge_targets(self, workspace_id, user_id):
        return self.targets.get((workspace_id, user_id), [])


class Repo:
    def __init__(self, communities=None, scenes=None):
        self.community_rows = communities if communities is not None else [community()]
        self.scene_rows = scenes if scenes is not None else [scene()]
        self.calls = []

    async def communities(self, user, limit):
        self.calls.append(("communities", user, limit))
        return self.community_rows[:limit]

    async def scenes(self, user, limit):
        self.calls.append(("scenes", user, limit))
        return self.scene_rows[:limit]


class Redis:
    def __init__(self):
        self.values = {}
        self.puts = 0

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex):
        assert ex > 0
        self.puts += 1
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)


def setup_service(**options):
    lookup, repo, redis = Lookup(), Repo(), Redis()
    config = Options(enabled=True, **options)
    cache = RedisDataCache(lambda: redis, config)
    service = SceneGraphService(AccessResolver(lookup), repo, config, cache)
    return service, lookup, repo, redis, cache


@pytest.mark.parametrize("parent,reason", [
    (None, "NO_PARENT"), ("", "NO_PARENT"), (" \t", "NO_PARENT"),
    (42, "INVALID_PARENT_ID"), (["c1"], "INVALID_PARENT_ID"),
    ("missing", "PARENT_NOT_FOUND"),
])
def test_unassigned_never_disappears_even_when_active(parent, reason):
    data = assemble([community()], [scene(parent=parent, community_status="ACTIVE")])
    assert data.statistics.total_scene_summaries == 1
    assert data.statistics.unassigned_scene_summaries == 1
    assert data.groups[-1].scenes[0].unassigned_reason == reason
    assert data.groups[0].scene_count == 0


def test_parent_trim_counts_whitelist_and_stored_count():
    data = assemble(
        [community(member_count=99, summary_embedding=[1.0])],
        [scene(parent=" c1 ", summary_embedding=[1.0], source_message_ids=["private"])],
    )
    assert len(data.groups) == 1
    assert data.groups[0].scene_count == 1
    assert data.groups[0].properties.member_count == 99
    assert data.groups[0].scenes[0].properties.scene_community_id == "c1"
    payload = encode_data(data)
    assert b"summary_embedding" not in payload and b"source_message_ids" not in payload


def test_timezone_sort_empty_groups_and_virtual_order():
    data = assemble(
        [community("empty", updated_at="2030-01-01T00:00:00Z"), community()],
        [scene("s2", ended_at="2026-10-23T08:30:00+08:00"),
         scene("s1", ended_at="2026-10-23T00:45:00Z"), scene("s3", None)],
    )
    assert [g.id for g in data.groups] == ["c1", "empty", "virtual:scene-community:unassigned"]
    assert [s.id for s in data.groups[0].scenes] == ["s1", "s2"]
    assert data.groups[0].scenes[1].properties.ended_at == STAMP_MS + 30 * 60_000
    assert data.groups[0].properties.member_count == 2


def test_empty_is_success_and_zero_statistics():
    data = assemble([], [])
    assert data.groups == []
    assert set(data.statistics.model_dump().values()) == {0}


@pytest.mark.parametrize("source,expected", [
    (STAMP, STAMP_MS),
    ("2026-10-23T08:00:00+08:00", STAMP_MS),
    ("2026-10-23T00:00:00.123999Z", STAMP_MS + 123),
    (datetime(2026, 10, 23), STAMP_MS),
    (datetime(2026, 10, 23, tzinfo=timezone.utc), STAMP_MS),
    (STAMP_MS, STAMP_MS),
    (None, None),
])
def test_database_times_are_projected_to_milliseconds(source, expected):
    data = assemble([community(created_at=source)], [scene(started_at=source)])
    assert data.groups[0].properties.created_at == expected
    assert data.groups[0].scenes[0].properties.started_at == expected


def test_neo4j_native_datetime_and_millisecond_tie_sort():
    from neo4j.time import DateTime
    data = assemble([community(created_at=DateTime(2026, 10, 23))], [
        scene("z", ended_at="2026-10-23T00:00:00.123999Z"),
        scene("a", ended_at="2026-10-23T00:00:00.123001Z"),
    ])
    assert data.groups[0].properties.created_at == STAMP_MS
    assert [item.id for item in data.groups[0].scenes] == ["a", "z"]


@pytest.mark.parametrize("invalid", [STAMP, str(STAMP_MS), STAMP_MS / 1000, 1792713600, True, 0, 10_000_000_000_000])
def test_response_model_rejects_non_millisecond_time_values(invalid):
    with pytest.raises(ValidationError):
        CommunityProperties(member_count=0, created_at=invalid)


@pytest.mark.parametrize("communities,scenes", [
    ([community(), community()], []),
    ([], [scene(), scene()]),
    ([community("virtual:bad")], []),
    ([], [scene(content=None)]),
    ([], [scene(ended_at="bad-date")]),
])
def test_bad_data_is_not_silently_discarded(communities, scenes):
    with pytest.raises(BusinessException) as caught:
        assemble(communities, scenes)
    assert caught.value.code == 10002


def test_response_invariants_reject_corrupt_cache():
    raw = assemble([community()], [scene()]).model_dump(mode="json")
    raw["statistics"]["total_scene_summaries"] = 100
    with pytest.raises(ValidationError):
        GraphData.model_validate(raw)


def test_merge_resolves_only_inside_workspace_and_detects_cycle():
    async def run():
        lookup = Lookup()
        target = uuid4()
        lookup.flags[(WORKSPACE, USER)] = False
        lookup.targets[(WORKSPACE, USER)] = [target]
        lookup.flags[(WORKSPACE, target)] = True
        resolver = AccessResolver(lookup)
        assert (await resolver.resolve(WORKSPACE, str(USER))).end_user_id == target
        with pytest.raises(BusinessException) as missing:
            await resolver.resolve(uuid4(), str(USER))
        assert missing.value.code == 4001
        lookup.flags[(WORKSPACE, target)] = False
        lookup.targets[(WORKSPACE, target)] = [USER]
        with pytest.raises(BusinessException) as cycle:
            await resolver.resolve(WORKSPACE, str(USER))
        assert cycle.value.code == 10002
    asyncio.run(run())


def test_cache_hit_still_checks_authorization():
    async def run():
        service, lookup, repo, redis, _ = setup_service()
        first = await service.get_graph(WORKSPACE, str(USER))
        assert len(repo.calls) == 2 and redis.puts == 1
        assert await service.get_graph(WORKSPACE, str(USER)) == first
        assert len(repo.calls) == 2 and lookup.calls == 2
        del lookup.flags[(WORKSPACE, USER)]
        with pytest.raises(BusinessException) as revoked:
            await service.get_graph(WORKSPACE, str(USER))
        assert revoked.value.code == 4001
        assert len(repo.calls) == 2
    asyncio.run(run())


def test_corrupt_cache_reloads_and_keys_isolate_workspaces():
    async def run():
        service, _, repo, redis, cache = setup_service()
        user = AuthorizedUser(WORKSPACE, USER)
        redis.values[cache.key(user)] = b'{"broken":true}'
        assert cache.key(user) != cache.key(AuthorizedUser(uuid4(), USER))
        result = await service.get_graph(WORKSPACE, str(USER))
        assert result.statistics.total_scene_summaries == 1
        assert len(repo.calls) == 2
    asyncio.run(run())


def test_old_string_timestamp_cache_namespace_is_not_reused():
    async def run():
        service, _, repo, redis, cache = setup_service()
        user = AuthorizedUser(WORKSPACE, USER)
        old_key = cache.key(user).replace(":v2-ms:", ":v1:")
        redis.values[old_key] = b'{"old":"ISO dates"}'
        result = await service.get_graph(WORKSPACE, str(USER))
        assert result.groups[0].properties.updated_at == STAMP_MS
        assert len(repo.calls) == 2 and redis.puts == 1
        assert old_key in redis.values
    asyncio.run(run())


def test_disabled_and_invalid_uuid_do_not_query_neo4j():
    async def run():
        lookup, repo = Lookup(), Repo()
        service = SceneGraphService(AccessResolver(lookup), repo, Options())
        with pytest.raises(BusinessException) as disabled:
            await service.get_graph(WORKSPACE, str(USER))
        assert HTTP_MAPPING[disabled.value.code] == 503
        with pytest.raises(BusinessException) as invalid:
            await service.get_graph(WORKSPACE, "not-a-uuid")
        assert invalid.value.code == 9601
        assert repo.calls == []
    asyncio.run(run())


def test_overflow_and_byte_limits_do_not_cache_partial_results():
    async def run():
        service, _, repo, redis, _ = setup_service(max_scenes=1)
        repo.scene_rows = [scene("s1"), scene("s2")]
        with pytest.raises(BusinessException) as large:
            await service.get_graph(WORKSPACE, str(USER))
        assert large.value.code == 1003 and redis.puts == 0
        service, _, repo, redis, _ = setup_service(max_bytes=10)
        with pytest.raises(BusinessException) as text:
            await service.get_graph(WORKSPACE, str(USER))
        assert text.value.code == 1003 and redis.puts == 0
    asyncio.run(run())


def test_read_failure_cancels_sibling_without_partial_response():
    async def run():
        started, cleaned = asyncio.Event(), asyncio.Event()
        class BrokenRepo:
            async def communities(self, user, limit):
                await started.wait()
                raise RuntimeError("private-query-contents")
            async def scenes(self, user, limit):
                started.set()
                try:
                    await asyncio.sleep(30)
                finally:
                    cleaned.set()
        service = SceneGraphService(AccessResolver(Lookup()), BrokenRepo(), Options(enabled=True))
        with pytest.raises(BusinessException) as error:
            await service.get_graph(WORKSPACE, str(USER))
        assert HTTP_MAPPING[error.value.code] == 503 and cleaned.is_set()
        assert "private-query" not in error.value.message
    asyncio.run(run())


def test_redis_timeout_falls_back_to_database():
    async def run():
        class SlowRedis(Redis):
            async def get(self, key):
                await asyncio.sleep(1)
        options = Options(enabled=True, cache_timeout=0.005)
        redis, repo = SlowRedis(), Repo()
        service = SceneGraphService(AccessResolver(Lookup()), repo, options, RedisDataCache(lambda: redis, options))
        assert (await service.get_graph(WORKSPACE, str(USER))).statistics.total_scene_summaries == 1
        assert len(repo.calls) == 2
    asyncio.run(run())


def test_repository_uses_parameterized_reads_and_whitelist():
    async def run():
        calls = []
        class Result:
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise StopAsyncIteration
        class Tx:
            async def run(self, query, **params):
                calls.append((query, params))
                return Result()
        class Connector:
            async def execute_read_transaction(self, callback, **kwargs):
                return await callback(Tx())
        repo = Neo4jRepository(Connector())
        user = AuthorizedUser(WORKSPACE, USER)
        await repo.communities(user, 1001)
        await repo.scenes(user, 5001)
        assert [c[1]["probe_limit"] for c in calls] == [1001, 5001]
        assert all(c[1]["end_user_id"] == str(USER) for c in calls)
        assert str(USER) not in SCENES_QUERY
        assert "summary_embedding" not in COMMUNITIES_QUERY + SCENES_QUERY
        assert "source_message_ids" not in SCENES_QUERY
        assert "community_status: 'ACTIVE'" not in SCENES_QUERY
    asyncio.run(run())
