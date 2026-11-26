"""Contract tests of the application-service callback read face.

The face keeps the transport of every other internal endpoint (internal token →
``MemoryPrincipal``) but speaks the host's body/response shape, including the
caller-owned ``config_id`` / ``draft`` / ``record_display`` / ``history`` /
``storage_type`` that the legacy internal read face used to fix.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from core_read_fakes import (
    FakeAlerts,
    FakeAnswers,
    FakeConfigPort,
    FakeEndUserPort,
    FakeHistory,
    FakeModelGateway,
    FakePrompts,
    FakeSearchFactory,
    FakeSideEffects,
)
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from src.app import create_app
from src.config import ProfileSettings
from src.core.application.read.context import ReadContextFactory
from src.core.composition import build_memory_read_module
from src.core.domain.models import ReadPrincipal

_TENANT = UUID(int=2)
_WORKSPACE = UUID(int=3)
_USER = UUID(int=9)
_END_USER = str(UUID(int=100))
_PATH = "/internal/v1/memory/read/app-service"


def _app(**overrides):
    app = create_app(settings=ProfileSettings.model_validate({}))
    ports = {
        "end_users": FakeEndUserPort(),
        "configs": FakeConfigPort(),
        "models": FakeModelGateway(),
        "prompts": FakePrompts(),
        "sessions": FakeHistory(),
        "answers": FakeAnswers(),
        "search_factory": FakeSearchFactory(),
        "side_effects": FakeSideEffects(),
        "alerts": FakeAlerts(),
    }
    ports.update(overrides)
    app.state.memory_read_module = build_memory_read_module(
        app.state.runtime, overrides=ports
    )
    return app


def _client(app, *, principal: bool = True) -> TestClient:
    client = TestClient(app)
    if principal:
        client.headers.update(
            {
                "x-source": "in_api",
                "x-tenant-id": str(_TENANT),
                "x-workspace-id": str(_WORKSPACE),
                "x-user-id": str(_USER),
            }
        )
    return client


def _body(**overrides):
    body = {"end_user_id": _END_USER, "query": "问题", "search_switch": "2"}
    body.update(overrides)
    return body


class _RecordingConfigPort(FakeConfigPort):
    def __init__(self) -> None:
        super().__init__()
        self.loaded: list[UUID] = []

    async def load_config(self, config_id: UUID):
        self.loaded.append(config_id)
        return self.config


# ── routing and authentication ──────────────────────────────────


def test_callback_route_is_registered() -> None:
    paths = {
        "/internal/v1" + context.route.path
        for context in iter_route_contexts(_app().routes)
    }

    assert _PATH in paths


def test_callback_requires_the_internal_principal() -> None:
    with _client(_app(), principal=False) as client:
        response = client.post(_PATH, json=_body())

    assert response.status_code == 401
    assert response.json()["code"] == 2001


def test_callback_validates_the_body_like_the_host() -> None:
    with _client(_app()) as client:
        missing_query = client.post(_PATH, json={"end_user_id": _END_USER})
        bad_storage = client.post(_PATH, json=_body(storage_type="dynamo"))
        bad_include = client.post(_PATH, json=_body(includes=["Nope"]))
        bad_limit = client.post(_PATH, json=_body(limit=0))

    for response in (missing_query, bad_storage, bad_include, bad_limit):
        assert response.status_code == 422
        assert response.json()["code"] == 1001


# ── response shape and caller-owned options ─────────────────────


def test_callback_returns_the_host_payload_shape() -> None:
    with _client(_app()) as client:
        response = client.post(_PATH, json=_body())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"code", "msg", "data", "error", "time"}
    assert body["code"] == 0
    assert set(body["data"]) == {"content", "memories"}
    assert isinstance(body["data"]["content"], str) and body["data"]["content"]
    assert body["data"]["memories"]
    item = body["data"]["memories"][0]
    # The callback returns the raw memory items, not the public projection.
    assert set(item) == {"source", "score", "content", "data", "query", "id"}


def test_callback_honours_the_named_configuration() -> None:
    configs = _RecordingConfigPort()
    requested = UUID(int=77)
    with _client(_app(configs=configs)) as client:
        response = client.post(_PATH, json=_body(config_id=str(requested)))

    assert response.status_code == 200
    assert configs.loaded == [requested]


def test_callback_honours_draft_and_record_display() -> None:
    side_effects = FakeSideEffects()
    app = _app(side_effects=side_effects)
    with _client(app) as client:
        draft = client.post(
            _PATH, json=_body(search_switch="1", draft=True, record_display=True)
        )
        live = client.post(
            _PATH, json=_body(search_switch="1", draft=False, record_display=True)
        )

    assert draft.status_code == 200 and live.status_code == 200
    # Two reads: only the non-draft one writes the short-term memo.
    assert len(side_effects.short_term_calls) == 1
    assert len(side_effects.display_calls) == 2


def test_callback_reports_an_unknown_end_user_in_the_envelope() -> None:
    app = _app(end_users=FakeEndUserPort(resolvable=False))
    with _client(app) as client:
        response = client.post(_PATH, json=_body())

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 4001
    assert body["data"] == {}


# ── context factory overrides (core level) ──────────────────────


def test_context_factory_applies_caller_overrides() -> None:
    factory = ReadContextFactory(FakeEndUserPort(), FakeConfigPort())

    ctx = asyncio.run(
        factory.build(
            principal=ReadPrincipal(workspace_id=_WORKSPACE, tenant_id=_TENANT),
            end_user_id=_END_USER,
            language="zh",
            draft=True,
            conversation_id="conv-1",
            app_id=UUID(int=55),
            config_id=UUID(int=77),
            storage_type="rag",
            user_rag_memory_id="rag-1",
        )
    )

    assert ctx.config is not None
    assert ctx.tenant_id == ctx.config.tenant_id == _TENANT
    assert ctx.storage_type.value == "rag"
    assert ctx.draft is True
    assert ctx.conversation_id == "conv-1"
    assert ctx.app_id == UUID(int=55)
    assert ctx.user_rag_memory_id == "rag-1"


def test_context_factory_falls_back_to_the_config_tenant() -> None:
    factory = ReadContextFactory(FakeEndUserPort(), FakeConfigPort())

    ctx = asyncio.run(
        factory.build(
            principal=ReadPrincipal(workspace_id=_WORKSPACE),
            end_user_id=_END_USER,
            language="zh",
        )
    )

    # A principal without a tenant (service channels) takes the config's.
    assert ctx.tenant_id == _TENANT
