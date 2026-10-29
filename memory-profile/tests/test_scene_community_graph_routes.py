"""Exercise the migrated internal route, identity boundary, and error contract."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from src.config import ProfileSettings
from src.error_handlers import register_error_handlers
from src.i18n import LanguageMiddleware, clear_i18n, configure_i18n
from src.middleware.auth import MemoryAuthConfig, MemoryAuthMiddleware
from src.modules.analytics import routes
from src.runtime import ProfileRuntime
from src.services import graph as graph_service
from src.services.end_user import EndUserRef, EndUserService
from src.services.scene_community_graph.service import SceneGraphService
from src.services.scene_community_graph.types import Options

PATH = "/internal/v1/memory/analytics/scene_community_graph"
WORKSPACE, USER = uuid4(), uuid4()


@pytest.fixture
def graph_app(monkeypatch):
    configure_i18n()
    class Lookup:
        calls = 0

        async def resolve_in_workspace(self, user_id, workspace_id):
            self.calls += 1
            if (workspace_id, user_id) == (WORKSPACE, USER):
                return EndUserRef(USER, WORKSPACE)
            return None

    class Repo:
        community_rows = [{"id": "c1", "properties": {"topic_name": "Project"}}]
        scene_rows = [
            {
                "id": "s1",
                "properties": {
                    "content": "Assigned scene",
                    "scene_community_id": "c1",
                    "created_at": "2026-10-23T00:00:00Z",
                },
            },
            {
                "id": "s2",
                "properties": {
                    "content": "Unassigned scene",
                    "scene_community_id": "missing",
                },
            },
        ]

        async def communities(self, user, limit):
            return self.community_rows

        async def scenes(self, user, limit):
            return self.scene_rows

    lookup, repo = Lookup(), Repo()
    service = SceneGraphService(lookup, repo, Options(enabled=True))
    monkeypatch.setattr(
        graph_service, "get_scene_community_graph_service", lambda runtime, end_users: service
    )
    app = FastAPI()
    app.state.runtime = ProfileRuntime(ProfileSettings())
    app.include_router(routes.router, prefix="/internal/v1")
    app.add_middleware(MemoryAuthMiddleware, config=MemoryAuthConfig())
    app.add_middleware(LanguageMiddleware)
    register_error_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service, lookup, repo
    clear_i18n()


def identity(source="in_api", workspace=WORKSPACE):
    return {
        "x-source": source,
        "x-user-id" if source == "in_api" else "x-api-key-id": str(uuid4()),
        "x-tenant-id": str(uuid4()),
        "x-workspace-id": str(workspace),
        "accept-language": "en",
    }


@pytest.mark.parametrize("source", ["in_api", "ex_api"])
def test_both_authenticated_faces_get_grouped_scenes(graph_app, source):
    client, _, _, _ = graph_app
    response = client.get(PATH, params={"end_user_id": str(USER)}, headers=identity(source))
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0 and body["msg"] == "OK" and body["error"] == ""
    assert 1_000_000_000_000 <= body["time"] <= 9_999_999_999_999
    real, unassigned = body["data"]["groups"]
    assert real["kind"] == "COMMUNITY" and real["scene_count"] == 1
    assert real["scenes"][0]["properties"]["created_at"] == 1792713600000
    assert unassigned["kind"] == "UNASSIGNED" and unassigned["virtual"] is True
    assert unassigned["scenes"][0]["unassigned_reason"] == "PARENT_NOT_FOUND"
    assert body["data"]["statistics"]["total_scene_summaries"] == 2


def test_missing_identity_is_rejected_before_any_user_lookup(graph_app):
    client, _, lookup, _ = graph_app
    response = client.get(PATH, params={"end_user_id": str(USER)})
    assert response.status_code == 401 and lookup.calls == 0


def test_required_query_and_invalid_uuid_are_rejected(graph_app):
    client, _, _, _ = graph_app
    missing = client.get(PATH, headers=identity())
    assert missing.status_code == 422 and missing.json()["code"] == 1001
    invalid = client.get(PATH, params={"end_user_id": "bad-id"}, headers=identity())
    assert invalid.status_code == 400 and invalid.json()["code"] == 9601


def test_foreign_workspace_returns_user_not_found(graph_app):
    client, _, _, _ = graph_app
    response = client.get(
        PATH,
        params={"end_user_id": str(USER)},
        headers=identity(workspace=uuid4()),
    )
    assert response.status_code == 200
    assert response.json()["code"] == 4001 and response.json()["data"] == {}


def test_empty_data_is_a_success_and_disabled_feature_is_unavailable(graph_app):
    client, service, _, repo = graph_app
    repo.community_rows, repo.scene_rows = [], []
    empty = client.get(PATH, params={"end_user_id": str(USER)}, headers=identity())
    assert empty.json()["code"] == 0 and empty.json()["data"]["groups"] == []
    assert all(value == 0 for value in empty.json()["data"]["statistics"].values())
    service.options = replace(service.options, enabled=False)
    disabled = client.get(PATH, params={"end_user_id": str(USER)}, headers=identity())
    assert disabled.status_code == 503 and disabled.json()["code"] == 10003
    assert disabled.json()["msg"] == "Scene community graph service is not enabled"


@pytest.mark.parametrize(
    "language,message",
    [("zh", "场景社区数据异常"), ("en", "Invalid scene community data")],
)
def test_bad_scene_data_uses_shared_localized_error_handler(graph_app, language, message):
    client, _, _, repo = graph_app
    repo.scene_rows = [{"id": "s1", "properties": {"content": {"private": "secret"}}}]
    response = client.get(
        PATH, params={"end_user_id": str(USER)},
        headers={**identity(), "X-Language-Type": language},
    )
    assert response.status_code == 500 and response.json()["code"] == 10002
    assert response.json()["msg"] == message
    assert response.json()["error"] == response.json()["msg"]
    assert "secret" not in response.text


def test_handler_validates_service_output_before_serializing(graph_app, monkeypatch):
    client, _, _, _ = graph_app

    class InvalidService:
        async def get_graph(self, workspace_id, requested_id):
            return {"groups": [], "statistics": "private-data"}

    monkeypatch.setattr(
        graph_service,
        "get_scene_community_graph_service",
        lambda runtime, end_users: InvalidService(),
    )
    response = client.get(PATH, params={"end_user_id": str(USER)}, headers=identity())
    assert response.status_code == 500 and response.json()["code"] == 10001
    assert "private-data" not in response.text


def test_internal_openapi_exposes_millisecond_response_and_required_query(graph_app):
    client, _, _, _ = graph_app
    spec = client.app.openapi()
    operation = spec["paths"][PATH]["get"]
    assert "requestBody" not in operation
    assert operation["parameters"][0]["name"] == "end_user_id"
    stamp = spec["components"]["schemas"]["GraphResponse"]["properties"]["time"]
    assert stamp["type"] == "integer" and stamp["format"] == "int64"
    assert stamp["minimum"] == 1_000_000_000_000


@pytest.mark.parametrize(
    "setting,value",
    [
        ("ENABLE_SCENE_COMMUNITY_GRAPH", "yes"),
        ("SCENE_GRAPH_CACHE_ENABLED", "invalid"),
        ("SCENE_GRAPH_MAX_SCENES", 0),
        ("SCENE_GRAPH_READ_BUDGET_SECONDS", float("nan")),
    ],
)
def test_profile_rejects_invalid_scene_graph_settings(setting, value):
    with pytest.raises(ValidationError):
        ProfileSettings(**{setting: value})


def test_profile_keeps_boolean_defaults_and_environment_names(monkeypatch):
    monkeypatch.delenv("ENABLE_SCENE_COMMUNITY_GRAPH", raising=False)
    monkeypatch.delenv("SCENE_GRAPH_CACHE_ENABLED", raising=False)
    settings = ProfileSettings()
    assert settings.enable_scene_community_graph is False
    assert settings.scene_graph_cache_enabled is True
    settings = ProfileSettings(ENABLE_SCENE_COMMUNITY_GRAPH="1", SCENE_GRAPH_CACHE_ENABLED="0")
    assert settings.enable_scene_community_graph is True
    assert settings.scene_graph_cache_enabled is False


def test_existing_shared_user_resolution_scopes_active_users_and_merge_targets():
    statements = []
    target = uuid4()

    class Postgres:
        @asynccontextmanager
        async def session(self):
            class Session:
                async def execute(self, statement):
                    statements.append(statement)
                    return SimpleNamespace(
                        mappings=lambda: SimpleNamespace(
                            first=lambda: {"id": target, "other_name": None}
                        ),
                    )

            yield Session()

    async def run():
        users = EndUserService.from_postgres(Postgres())
        resolved = await users.resolve_in_workspace(USER, WORKSPACE)
        assert resolved == EndUserRef(target, WORKSPACE)
        assert await users.resolve_in_workspace("bad-id", WORKSPACE) is None

    asyncio.run(run())
    for statement in statements:
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled).lower()
        assert "workspaces.is_active is true" in sql
        assert "end_users.is_active is true" in sql
        assert "union all" in sql and "end_user_merge.workspace_id" in sql
        assert WORKSPACE in compiled.params.values() and USER in compiled.params.values()
        assert "memory_insight" not in sql and "user_summary" not in sql
    assert len(statements) == 1
