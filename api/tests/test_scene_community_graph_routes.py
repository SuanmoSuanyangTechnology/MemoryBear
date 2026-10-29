"""Verify that both API faces forward graph queries to memory-profile."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.controllers import memory_analytics_controller as manager
from app.controllers.service import user_memory_api_controller as service
from app.integrations.profile import proxy
from app.integrations.profile.contracts import MemoryProfileAuthSource, MemoryProfileRawResponse
from app.main import app
from app.services.api_key_service import ApiKeyAuthService, RateLimiterService

MANAGER_PATH = "/api/memory/analytics/scene_community_graph"
SERVICE_PATH = "/v1/memory/analytics/scene_community_graph"


@pytest.fixture
def forwarding(monkeypatch):
    calls = []
    body = json.dumps({
        "code": 0, "msg": "OK", "error": "", "time": 1792713600000,
        "data": {"groups": [], "statistics": {
            "total_scene_communities": 0, "total_scene_summaries": 0,
            "assigned_scene_summaries": 0, "unassigned_scene_summaries": 0,
            "total_groups": 0,
        }},
    }).encode()

    class Client:
        async def request(self, **kwargs):
            calls.append(kwargs)
            return MemoryProfileRawResponse(body=body, media_type="application/json")

    monkeypatch.setattr(proxy, "is_memory_profile_enabled", lambda: True)
    monkeypatch.setattr(proxy, "get_memory_profile_client", Client)
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), current_workspace_id=uuid4())
    return calls, body, user


def request(path, *, api_key=False):
    headers = [(b"x-workspace-id", str(uuid4()).encode())]
    if api_key:
        headers.append((b"x-api-key", b"test-key"))
    return Request({
        "type": "http", "method": "GET", "path": path,
        "headers": headers, "scheme": "http", "server": ("test", 80),
    })


def test_routes_keep_both_faces_and_legacy_community_graph():
    for path in (MANAGER_PATH, SERVICE_PATH,
                 "/api/memory/analytics/community_graph",
                 "/v1/memory/analytics/community_graph"):
        assert sum(route.path == path and "GET" in route.methods for route in app.routes) == 1
    spec = app.openapi()
    for path in (MANAGER_PATH, SERVICE_PATH):
        operation = spec["paths"][path]["get"]
        assert "requestBody" not in operation
        parameter = next(p for p in operation["parameters"] if p["name"] == "end_user_id")
        assert parameter["in"] == "query" and parameter["required"]


def test_manager_forwards_original_user_id_and_trusted_identity(forwarding):
    calls, body, user = forwarding
    requested = str(uuid4())
    reply = asyncio.run(manager.get_scene_community_graph_for_manager(
        end_user_id=requested, current_user=user, request=request(MANAGER_PATH),
    ))
    assert reply.body == body
    assert len(calls) == 1
    assert calls[0]["path"] == MANAGER_PATH
    assert calls[0]["source"] == MemoryProfileAuthSource.INTERNAL
    assert calls[0]["actor_id"] == str(user.id)
    assert calls[0]["workspace_id"] == str(user.current_workspace_id)
    assert calls[0]["tenant_id"] == str(user.tenant_id)
    assert calls[0]["query"] == {"end_user_id": requested}
    assert calls[0]["raw"] is True


def test_disabled_proxy_returns_unavailable_without_local_graph_query(monkeypatch, forwarding):
    calls, _, user = forwarding
    monkeypatch.setattr(proxy, "is_memory_profile_enabled", lambda: False)
    reply = asyncio.run(manager.get_scene_community_graph_for_manager(
        end_user_id=str(uuid4()), current_user=user, request=request(MANAGER_PATH),
    ))
    assert reply["code"] == 10003 and reply["data"] == {}
    assert calls == []


def test_profile_business_error_is_forwarded_without_reencoding(monkeypatch, forwarding):
    _, _, user = forwarding
    payload = b'{"code":4001,"msg":"User not found","data":{},"error":"","time":1792713600000}'

    class Client:
        async def request(self, **kwargs):
            return MemoryProfileRawResponse(payload, "application/json")

    monkeypatch.setattr(proxy, "get_memory_profile_client", Client)
    reply = asyncio.run(manager.get_scene_community_graph_for_manager(
        end_user_id=str(uuid4()), current_user=user, request=request(MANAGER_PATH),
    ))
    assert reply.body == payload


def test_api_key_face_forwards_key_identity_without_resolving_end_user(monkeypatch, forwarding):
    from app import db as db_module

    calls, body, user = forwarding
    key_id, creator = uuid4(), uuid4()

    class FakeDb:
        async def commit(self):
            pass

        async def execute(self, statement):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    @asynccontextmanager
    async def fake_db():
        yield FakeDb()

    async def validate(db, key):
        assert key == "test-key"
        return SimpleNamespace(
            id=key_id, workspace_id=user.current_workspace_id, type="service",
            scopes=["memory"], resource_id=None, created_by=creator,
        )

    async def published(db, key):
        pass

    async def limits(self, key, db=None):
        return True, "", {}

    async def snapshot(db, auth):
        assert auth.api_key_id == key_id
        return user

    async def forbidden_lookup(*args, **kwargs):
        raise AssertionError("End-user resolution belongs to memory-profile")

    monkeypatch.setattr(db_module, "get_async_db_context", fake_db)
    monkeypatch.setattr(service, "get_async_db_context", fake_db)
    monkeypatch.setattr(service, "get_current_user_snapshot_from_api_key_async", snapshot)
    monkeypatch.setattr(service, "validate_end_user_in_workspace_async", forbidden_lookup)
    monkeypatch.setattr(ApiKeyAuthService, "validate_api_key_async", validate)
    monkeypatch.setattr(ApiKeyAuthService, "check_app_published_async", published)
    monkeypatch.setattr(ApiKeyAuthService, "check_scope", lambda key, scope: True)
    monkeypatch.setattr(RateLimiterService, "check_all_limits", limits)
    requested = str(uuid4())
    reply = asyncio.run(service.get_scene_community_graph_for_service(
        request=request(SERVICE_PATH, api_key=True), end_user_id=requested,
    ))
    assert reply.body == body
    assert len(calls) == 1
    assert calls[0]["path"] == SERVICE_PATH
    assert calls[0]["source"] == MemoryProfileAuthSource.APIKEY
    assert calls[0]["actor_id"] == str(key_id)
    assert calls[0]["workspace_id"] == str(user.current_workspace_id)
    assert calls[0]["query"] == {"end_user_id": requested}
