from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.i18n.middleware import LanguageMiddleware
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.display import routes as display
from src.modules.display.handler import MemoryDisplayHandler
from src.modules.implicit_memory import routes as implicit
from src.modules.implicit_memory.handler import ImplicitMemoryHandler
from src.modules.perceptual import routes as perceptual
from src.modules.perceptual.handler import PerceptualMemoryHandler


@pytest.fixture
def routes(monkeypatch):
    app = FastAPI()
    app.add_middleware(LanguageMiddleware)
    principal = MemoryPrincipal(
        actor_id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        source="in_api",
        actor_kind="actor",
        trace_id="test",
    )
    app.dependency_overrides[require_memory_context] = lambda: principal
    display_service = SimpleNamespace(
        **{
            name: AsyncMock(return_value=([], 0))
            for name in ["query_written", "query_retrieved", "query_engines", "query_all"]
        }
    )
    perceptual_service = SimpleNamespace(
        count=AsyncMock(
            return_value={"total": 0, "by_type": dict(vision=0, audio=0, text=0, conversation=0)}
        ),
        timeline=AsyncMock(
            return_value=dict(total=0, page=1, page_size=10, total_pages=0, memories=[])
        ),
        latest=AsyncMock(return_value=None),
    )
    implicit_service = SimpleNamespace(
        check_data=AsyncMock(return_value=False),
        preferences=AsyncMock(return_value=[]),
        portrait=AsyncMock(return_value={"custom": {"axis": 1}}),
        interest_areas=AsyncMock(return_value={}),
        habits=AsyncMock(return_value=[]),
        aggregate=AsyncMock(
            return_value=dict(
                end_user_id=str(uuid4()), preferences=[], habits=[], portrait={}, interest_areas={}
            )
        ),
    )
    monkeypatch.setattr(
        display, "_handler", lambda _: MemoryDisplayHandler(service=display_service)
    )
    monkeypatch.setattr(
        perceptual, "_handler", lambda _: PerceptualMemoryHandler(service=perceptual_service)
    )
    monkeypatch.setattr(
        implicit, "_handler", lambda _: ImplicitMemoryHandler(service=implicit_service)
    )
    for router in [display.router, perceptual.router, implicit.router]:
        app.include_router(router, prefix="/internal/v1")
    return TestClient(app), display_service, str(uuid4())


@pytest.mark.parametrize("slug", ["written", "retrieved", "engines", "all"])
def test_display_wire_envelope_and_accept_language(routes, slug):
    client, service, uid = routes
    response = client.get(
        f"/internal/v1/memory-display/{slug}",
        params={"timezone": "UTC", "end_user_id": uid},
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"] == {
        "page": {"page": 1, "pagesize": 10, "total": 0, "hasnext": False},
        "items": [],
    }
    if slug in ["engines", "all"]:
        assert getattr(service, f"query_{slug}").await_args.kwargs["language"] == "en"


@pytest.mark.parametrize("slug", ["count", "timeline", "last_visual", "last_listen", "last_text"])
def test_perceptual_routes_keep_empty_shapes(routes, slug):
    client, _, uid = routes
    response = client.get(f"/internal/v1/memory/perceptual/{uid}/{slug}")
    assert response.status_code == 200
    data = response.json()["data"]
    if slug.startswith("last_"):
        assert data == {}
    elif slug == "timeline":
        assert data["memories"] == []
        assert data["page_size"] == 10
    else:
        assert data["total"] == 0


@pytest.mark.parametrize(
    "slug", ["check-data", "preferences", "portrait", "interest-areas", "habits", "aggregate"]
)
def test_implicit_routes_keep_existing_payloads(routes, slug):
    client, _, uid = routes
    path = f"/internal/v1/memory/implicit-memory/{slug}/{uid}"
    if slug == "aggregate":
        path = "/internal/v1/memory/implicit-memory?end_user_id=" + uid
    response = client.get(path)
    assert response.status_code == 200
    data = response.json()["data"]
    if slug == "check-data":
        assert data == {"exists": False}
    elif slug == "portrait":
        assert data == {"custom": {"axis": 1}}
    elif slug in ["preferences", "habits"]:
        assert data == []


def test_read_routes_reject_invalid_pagination(routes):
    client, _, _ = routes
    assert client.get("/internal/v1/memory-display/written?page=0").status_code == 422
    assert client.get("/internal/v1/memory-display/written?pagesize=101").status_code == 422
