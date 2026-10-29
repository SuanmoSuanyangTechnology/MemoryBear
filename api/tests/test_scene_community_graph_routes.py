"""Protect the integrated routes and shared response contract."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotenv import dotenv_values

PROJECT = Path(__file__).resolve().parents[2]
for path in (PROJECT / '.env', PROJECT / 'api/.env'):
    os.environ.update({k: v for k, v in dotenv_values(path).items() if v is not None})

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.config import settings
settings.LOG_FORMAT = os.environ['LOG_FORMAT']
from app.controllers import scene_community_graph_response as response_module
from app.main import app
from app.services.scene_community_graph_assembler import assemble
from app.controllers.service.user_memory_api_controller import get_scene_community_graph_for_service
from app.services.api_key_service import ApiKeyAuthService, RateLimiterService
from starlette.requests import Request


def test_new_routes_registered_once_and_legacy_routes_preserved():
    paths = (
        '/api/memory/analytics/scene_community_graph',
        '/v1/memory/analytics/scene_community_graph',
        '/api/memory/analytics/community_graph',
        '/v1/memory/analytics/community_graph',
    )
    for path in paths:
        assert sum(route.path == path and 'GET' in route.methods for route in app.routes) == 1
    spec = app.openapi()
    for path in paths[:2]:
        assert 'requestBody' not in spec['paths'][path]['get']
    time = spec['components']['schemas']['GraphResponse']['properties']['time']
    assert time['type'] == 'integer' and time['format'] == 'int64'
    assert time['minimum'] == 1_000_000_000_000


def test_response_helper_validates_before_api_key_wrapper(monkeypatch):
    class Service:
        async def get_graph(self, workspace_id, requested_id):
            return assemble([], [])

    monkeypatch.setattr(response_module, 'get_scene_community_graph_service', lambda: Service())
    body = asyncio.run(response_module.scene_community_graph_response(uuid4(), str(uuid4())))
    assert body['code'] == 0 and body['data']['groups'] == []
    assert 1_000_000_000_000 <= body['time'] <= 9_999_999_999_999

    class InvalidService:
        async def get_graph(self, workspace_id, requested_id):
            return {'groups': [], 'statistics': 'bad-private-data'}

    monkeypatch.setattr(response_module, 'get_scene_community_graph_service', lambda: InvalidService())
    with pytest.raises(BusinessException) as caught:
        asyncio.run(response_module.scene_community_graph_response(uuid4(), str(uuid4())))
    assert caught.value.code == BizCode.INTERNAL_ERROR
    assert 'bad-private-data' not in caught.value.message


def test_api_key_entry_uses_shared_service(monkeypatch):
    from app import db as db_module
    workspace = uuid4()

    class FakeDb:
        async def commit(self):
            pass

        async def execute(self, statement):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    @asynccontextmanager
    async def fake_db():
        yield FakeDb()

    async def validate(db, key):
        assert key == 'test-key'
        return SimpleNamespace(id=uuid4(), workspace_id=workspace, type='service',
                               scopes=['memory'], resource_id=None, created_by=uuid4())

    async def published(db, key):
        pass

    async def limits(self, key, db=None):
        return True, '', {}

    class Service:
        async def get_graph(self, workspace_id, requested_id):
            assert workspace_id == workspace
            return assemble([], [])

    monkeypatch.setattr(db_module, 'get_async_db_context', fake_db)
    monkeypatch.setattr(ApiKeyAuthService, 'validate_api_key_async', validate)
    monkeypatch.setattr(ApiKeyAuthService, 'check_app_published_async', published)
    monkeypatch.setattr(ApiKeyAuthService, 'check_scope', lambda key, scope: True)
    monkeypatch.setattr(RateLimiterService, 'check_all_limits', limits)
    monkeypatch.setattr(response_module, 'get_scene_community_graph_service', lambda: Service())
    request = Request({'type': 'http', 'method': 'GET', 'path': '/v1/memory/analytics/scene_community_graph',
                       'headers': [(b'x-api-key', b'test-key')], 'scheme': 'http', 'server': ('test', 80)})
    reply = asyncio.run(get_scene_community_graph_for_service(request=request, end_user_id=str(uuid4())))
    body = json.loads(reply.body)
    assert reply.status_code == 200 and body['code'] == 0
    assert body['data']['statistics']['total_scene_summaries'] == 0
