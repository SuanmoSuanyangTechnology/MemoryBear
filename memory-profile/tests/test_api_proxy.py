import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))


@pytest.fixture
def proxy_module():
    return importlib.import_module("app.integrations.profile.proxy")


def request():
    return Request(
        {
            "type": "http",
            "path": "/api/memory-display/written",
            "method": "GET",
            "headers": [(b"accept-language", b"en")],
        }
    )


async def test_disabled_proxy_executes_legacy(proxy_module, monkeypatch):
    legacy = AsyncMock(return_value={"local": True})
    monkeypatch.setattr(proxy_module, "is_memory_profile_enabled", lambda: False)

    @proxy_module.memory_profile_proxy()
    async def handler(request=None, current_user=None):
        return await legacy()

    assert await handler() == {"local": True}
    legacy.assert_awaited_once()


async def test_enabled_proxy_forwards_language_and_limits(proxy_module, monkeypatch):
    client = SimpleNamespace(request=AsyncMock(return_value={"code": 0}))
    monkeypatch.setattr(proxy_module, "is_memory_profile_enabled", lambda: True)
    monkeypatch.setattr(proxy_module, "get_memory_profile_client", lambda: client)
    legacy = AsyncMock()

    @proxy_module.memory_profile_proxy(query_fields=("pagesize",), upper_bounds={"pagesize": 100})
    async def handler(request=None, current_user=None, pagesize=10):
        return await legacy()

    user = SimpleNamespace(id=uuid4(), current_workspace_id=uuid4(), tenant_id=uuid4())
    assert await handler(request=request(), current_user=user, pagesize=1000) == {"code": 0}
    assert client.request.await_args.kwargs["language_type"] == "en"
    assert client.request.await_args.kwargs["query"] == {"pagesize": 100}
    legacy.assert_not_awaited()


async def test_remote_failure_does_not_retry_legacy(proxy_module, monkeypatch):
    client = SimpleNamespace(
        request=AsyncMock(side_effect=proxy_module.MemoryProfileClientError("offline"))
    )
    monkeypatch.setattr(proxy_module, "is_memory_profile_enabled", lambda: True)
    monkeypatch.setattr(proxy_module, "get_memory_profile_client", lambda: client)
    legacy = AsyncMock()

    @proxy_module.memory_profile_proxy()
    async def handler(request=None, current_user=None):
        return await legacy()

    user = SimpleNamespace(id=uuid4(), current_workspace_id=uuid4(), tenant_id=uuid4())
    result = await handler(request=request(), current_user=user)
    assert result["code"] == proxy_module.BizCode.SERVICE_UNAVAILABLE
    legacy.assert_not_awaited()
