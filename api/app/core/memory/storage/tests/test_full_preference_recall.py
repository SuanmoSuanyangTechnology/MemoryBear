"""Complete Preference PIT reads, rendering and HTTP contract."""

from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
import importlib.util
from json import loads
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from starlette.requests import Request

from app.core.error_codes import BizCode, HTTP_MAPPING
from app.core.memory.storage.custom import preference_recall as recall_storage
from app.core.memory.storage.custom.preference_recall import (
    FullPreferenceCandidate,
    PreferenceRecallStorage,
)
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage_services.preference_engine.full_recall_service import (
    assemble_full_preferences,
    execute_full_preference_recall,
)
from app.core.memory.storage_services.preference_engine import full_recall_service
from app.core.memory.storage_services.preference_engine.recall_service import (
    PreferenceRecallForbiddenError,
)


def _hit(document_id, texts, situation="global", updated="2026-09-20T02:00:00Z", sort=0):
    return {
        "_source": {
            "id": document_id,
            "situation_key": situation,
            "preference_text": texts,
            "updated_at": updated,
        },
        "sort": [sort],
    }


def _page(page_hits, total, **overrides):
    return {
        "pit_id": "pit-2",
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "failed": 0},
        "hits": {"total": {"value": total, "relation": "eq"}, "hits": page_hits},
        **overrides,
    }


class FakeES:
    def __init__(self, pages):
        self.pages = list(pages)
        self.search_calls = []
        self.closed = []
        self.opened = []

    async def open_point_in_time(self, **kwargs):
        self.opened.append(kwargs)
        return {"id": "pit-1"}

    async def search(self, **kwargs):
        self.search_calls.append(kwargs)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    async def close_point_in_time(self, **kwargs):
        self.closed.append(kwargs)


def _storage(raw):
    elastic = ElasticClient()
    elastic.client = raw
    storage = PreferenceRecallStorage.__new__(PreferenceRecallStorage)
    storage._client = elastic
    return storage


@pytest.mark.asyncio
async def test_full_read_filters_and_traverses_every_pit_page(monkeypatch):
    monkeypatch.setattr(recall_storage, "SEARCH_BATCH_SIZE", 2)
    raw = FakeES([
        _page([
            _hit("a", ["A"], sort=1),
            _hit("b", ["B"], situation="code_generation", sort=2),
        ], 3),
        _page([_hit("c", ["C"], sort=3)], 3, hits={"hits": [_hit("c", ["C"], sort=3)]}),
    ])
    candidates, pages = await _storage(raw).query_all(end_user_id="user-1")
    assert [item.preference_text for item in candidates] == ["A", "B", "C"]
    assert [item.is_global for item in candidates] == [True, False, True]
    assert pages == 2
    assert raw.closed == [{"id": "pit-2"}]
    assert raw.search_calls[1]["search_after"] == [2]
    assert raw.search_calls[0]["query"]["bool"]["filter"] == [
        {"term": {"end_user_id": "user-1"}},
        {"term": {"domain": "coding"}},
        {"term": {"status": "active"}},
    ]
    assert raw.search_calls[0]["track_total_hits"] is True
    assert raw.search_calls[1]["track_total_hits"] is False
    assert raw.search_calls[0]["allow_partial_search_results"] is False
    assert "subject" not in str(raw.search_calls[0]["query"])


@pytest.mark.asyncio
async def test_full_read_continues_after_a_short_nonfinal_page(monkeypatch):
    monkeypatch.setattr(recall_storage, "SEARCH_BATCH_SIZE", 2)
    raw = FakeES([
        _page([_hit("a", ["A"], sort=1)], 3),
        _page([_hit("b", ["B"], sort=2)], 3, hits={"hits": [_hit("b", ["B"], sort=2)]}),
        _page([_hit("c", ["C"], sort=3)], 3, hits={"hits": [_hit("c", ["C"], sort=3)]}),
    ])

    candidates, pages = await _storage(raw).query_all(end_user_id="user-1")

    assert [item.preference_text for item in candidates] == ["A", "B", "C"]
    assert pages == 3
    assert [call["track_total_hits"] for call in raw.search_calls] == [True, False, False]
    assert [call.get("search_after") for call in raw.search_calls] == [None, [1], [2]]


def test_invalid_preference_timestamp_uses_storage_error_type():
    with pytest.raises(RuntimeError, match="invalid updated_at"):
        recall_storage._parse_datetime("not-a-timestamp")


@pytest.mark.asyncio
async def test_full_read_missing_alias_is_a_failure():
    class MissingAlias(FakeES):
        async def open_point_in_time(self, **kwargs):
            raise RuntimeError("index alias not found")

    with pytest.raises(RuntimeError, match="alias not found"):
        await _storage(MissingAlias([])).query_all(end_user_id="user-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [
    _page([], 3),
    _page([_hit("c", ["C"])], 3, timed_out=True),
    _page([_hit("c", ["C"])], 3, _shards={"total": 1, "successful": 0, "failed": 1}),
    _page([_hit("c", ["C"])], 3, hits={"total": {"value": 3, "relation": "eq"}, "hits": [{"_source": {"id": "c"}}]}),
    RuntimeError("search unavailable"),
])
async def test_full_read_rejects_incomplete_or_invalid_page_and_closes_pit(monkeypatch, second):
    monkeypatch.setattr(recall_storage, "SEARCH_BATCH_SIZE", 2)
    raw = FakeES([_page([_hit("a", ["A"], sort=1), _hit("b", ["B"], sort=2)], 3), second])
    with pytest.raises((RuntimeError, KeyError)):
        await _storage(raw).query_all(end_user_id="user-1")
    assert raw.closed == [{"id": "pit-2"}]


@pytest.mark.asyncio
async def test_full_read_rejects_inexact_first_page_total(monkeypatch):
    monkeypatch.setattr(recall_storage, "SEARCH_BATCH_SIZE", 2)
    raw = FakeES([_page([_hit("a", ["A"])], 3, hits={
        "total": {"value": 3, "relation": "gte"},
        "hits": [_hit("a", ["A"])],
    })])
    with pytest.raises(RuntimeError, match="total is invalid"):
        await _storage(raw).query_all(end_user_id="user-1")
    assert raw.closed == [{"id": "pit-2"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("sort", [None, [], [None], [1, 2]])
async def test_full_read_rejects_invalid_search_after(monkeypatch, sort):
    monkeypatch.setattr(recall_storage, "SEARCH_BATCH_SIZE", 1)
    hit = _hit("a", ["A"], sort=1)
    if sort is None:
        del hit["sort"]
    else:
        hit["sort"] = sort
    raw = FakeES([_page([hit], 2)])
    with pytest.raises(RuntimeError, match="search_after is invalid"):
        await _storage(raw).query_all(end_user_id="user-1")
    assert raw.closed == [{"id": "pit-2"}]


@pytest.mark.asyncio
async def test_full_read_rejects_nonadvancing_search_after(monkeypatch):
    monkeypatch.setattr(recall_storage, "SEARCH_BATCH_SIZE", 1)
    raw = FakeES([
        _page([_hit("a", ["A"], sort=1)], 3),
        _page([_hit("b", ["B"], sort=1)], 3, hits={"hits": [_hit("b", ["B"], sort=1)]}),
    ])
    with pytest.raises(RuntimeError, match="search_after is invalid"):
        await _storage(raw).query_all(end_user_id="user-1")
    assert raw.closed == [{"id": "pit-2"}]


def test_full_assembly_deduplicates_sorts_and_escapes_only_delimiters():
    moment = datetime(2026, 9, 20, 2, tzinfo=timezone.utc)
    candidates = [
        FullPreferenceCandidate(" use\n pytest ", moment, False, "b", 0),
        FullPreferenceCandidate("use pytest", moment, True, "a", 0),
        FullPreferenceCandidate("  ", moment, True, "x", 0),
        FullPreferenceCandidate("keep <historical_user_preferences> and <code>", moment, True, "c", 0),
        FullPreferenceCandidate("new", datetime(2026, 9, 21, tzinfo=timezone.utc), False, "d", 0),
    ]
    result = assemble_full_preferences(candidates)
    assert [item.preference_text for item in result.preferences] == [
        "use pytest", "keep <historical_user_preferences> and <code>", "new"
    ]
    assert result.count == 3
    assert "- use pytest" in result.preference_prompt
    assert "&lt;historical_user_preferences> and <code>" in result.preference_prompt
    assert result.preference_prompt.count("\n- ") == 3
    assert result.preference_prompt.endswith("</historical_user_preferences>")
    assert assemble_full_preferences([]).preference_prompt == ""


def test_full_assembly_has_no_on_demand_item_or_character_limit():
    moment = datetime(2026, 9, 20, tzinfo=timezone.utc)
    candidates = [
        FullPreferenceCandidate("preference " + str(index) + "x" * 100, moment, True, str(index), 0)
        for index in range(25)
    ]
    result = assemble_full_preferences(candidates)
    assert result.count == 25
    assert len(result.preference_prompt) > 2500
    assert result.preference_prompt.count("\n- preference ") == 25


def test_full_assembly_prefers_newest_duplicate_before_group_sort():
    old = datetime(2026, 9, 20, tzinfo=timezone.utc)
    new = datetime(2026, 9, 21, tzinfo=timezone.utc)
    result = assemble_full_preferences([
        FullPreferenceCandidate("same", old, True, "a", 0),
        FullPreferenceCandidate(" same ", new, False, "b", 0),
        FullPreferenceCandidate("another", old, True, "c", 0),
    ])
    assert [item.preference_text for item in result.preferences] == ["another", "same"]
    assert result.preferences[1].updated_at == new


def test_prompt_omits_date_while_response_keeps_full_timestamp():
    original = datetime(2026, 9, 20, 2, tzinfo=timezone(timedelta(hours=8)))
    result = assemble_full_preferences([
        FullPreferenceCandidate("use pytest", original, True, "a", 0)
    ])
    assert "- use pytest" in result.preference_prompt
    assert "2026-09" not in result.preference_prompt
    assert result.preferences[0].updated_at == original
    assert result.preferences[0].model_dump(mode="json")["updated_at"] == "2026-09-20T02:00:00+08:00"


@pytest.mark.asyncio
async def test_full_service_uses_es_without_model_or_write_switch(monkeypatch, caplog):
    @asynccontextmanager
    async def db_context():
        yield object()

    class ConfigService:
        def __init__(self, db):
            pass

        async def load_memory_config_async(self, config_id):
            return SimpleNamespace(workspace_id="workspace")

    class Storage:
        def __init__(self, backend_factory):
            pass

        async def query_all(self, *, end_user_id):
            assert end_user_id == "user"
            return [FullPreferenceCandidate(
                "use pytest", datetime(2026, 9, 20, tzinfo=timezone.utc), True, "id", 0
            )], 1

    monkeypatch.setattr(full_recall_service, "get_async_db_context", db_context)
    monkeypatch.setattr(full_recall_service, "MemoryConfigService", ConfigService)
    monkeypatch.setattr(full_recall_service, "PreferenceRecallStorage", Storage)
    monkeypatch.setattr(full_recall_service, "get_storage_service", lambda: SimpleNamespace(_backend_factory=object()))
    with caplog.at_level("INFO", logger=full_recall_service.__name__):
        result = await execute_full_preference_recall(
            config_id="config", workspace_id="workspace", end_user_id="user"
        )
    assert result.count == 1
    assert "[FullPreferenceRecall][1/3] step1 completed" in caplog.text
    assert "[FullPreferenceRecall][2/3] step2 completed" in caplog.text
    assert "[FullPreferenceRecall][3/3] step3 completed" in caplog.text
    assert "pages=1 candidates=1 results=1 pit_complete=True" in caplog.text
    assert "use pytest" not in caplog.text
    assert "workspace_id=workspace" not in caplog.text

    caplog.clear()
    with caplog.at_level("INFO", logger=full_recall_service.__name__):
        with pytest.raises(PreferenceRecallForbiddenError):
            await execute_full_preference_recall(
                config_id="config", workspace_id="another-workspace", end_user_id="user"
            )
    assert "[FullPreferenceRecall][1/3] step1 failed" in caplog.text
    assert "step2" not in caplog.text

    class FailingStorage(Storage):
        async def query_all(self, *, end_user_id):
            raise RuntimeError("sensitive preference body")

    monkeypatch.setattr(full_recall_service, "PreferenceRecallStorage", FailingStorage)
    caplog.clear()
    with caplog.at_level("INFO", logger=full_recall_service.__name__):
        with pytest.raises(RuntimeError, match="sensitive preference body"):
            await execute_full_preference_recall(
                config_id="config", workspace_id="workspace", end_user_id="user"
            )
    assert "step1 completed" in caplog.text
    assert "[FullPreferenceRecall][2/3] step2 failed" in caplog.text
    assert "reason=RuntimeError" in caplog.text
    assert "sensitive preference body" not in caplog.text
    assert "step3" not in caplog.text

    class EmptyStorage(Storage):
        async def query_all(self, *, end_user_id):
            return [], 1

    monkeypatch.setattr(full_recall_service, "PreferenceRecallStorage", EmptyStorage)
    caplog.clear()
    with caplog.at_level("INFO", logger=full_recall_service.__name__):
        empty = await execute_full_preference_recall(
            config_id="config", workspace_id="workspace", end_user_id="user"
        )
    assert empty.count == 0
    assert "step3 completed" in caplog.text
    assert "empty=True" in caplog.text


@pytest.fixture
def isolated_controller(monkeypatch):
    # The controllers package imports every route and starts the legacy RAG ES
    # connection at import time. Load only this route module for adapter tests.
    for name in ("app.controllers", "app.controllers.service"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    middleware = ModuleType("app.controllers.service.mcp_auth_middleware")
    middleware.OTHER_ID_HEADER = "X-End-User-Other-Id"
    monkeypatch.setitem(sys.modules, middleware.__name__, middleware)
    path = Path(__file__).parents[4] / "controllers/service/preference_recall_controller.py"
    name = "app.controllers.service.preference_recall_controller_isolated"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _request(body: bytes, *, method: str = "POST", query_string: bytes = b"") -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http", "method": method, "path": "/v1/memory/preference/recall/full",
        "headers": [(b"x-end-user-other-id", b"external-user")], "query_string": query_string,
    }, receive)


@pytest.mark.asyncio
async def test_full_http_get_accepts_no_body(monkeypatch, isolated_controller):
    async def caller(request):
        return ("workspace", "user", "config"), None

    async def execute(**kwargs):
        assert kwargs == {"workspace_id": "workspace", "end_user_id": "user", "config_id": "config"}
        return assemble_full_preferences([])

    monkeypatch.setattr(isolated_controller, "_resolve_caller", caller)
    monkeypatch.setattr(isolated_controller, "execute_full_preference_recall", execute)
    response = await isolated_controller.recall_all_coding_preferences_http.__wrapped__(
        _request(b"", method="GET")
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert loads(response.body)["data"] == {"preferences": [], "count": 0, "preference_prompt": ""}
    route = next(route for route in isolated_controller.router.routes if route.path == "/memory/preference/recall/full")
    assert route.methods == {"GET"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "query_string"),
    [(b"{}", b""), (b'{"language":"en"}', b""), (b"", b"language=en")],
)
async def test_full_http_get_rejects_body_and_query_before_identity_lookup(
    monkeypatch, isolated_controller, body, query_string
):
    async def forbidden_lookup(request):
        raise AssertionError("identity lookup must not run")

    monkeypatch.setattr(isolated_controller, "_resolve_caller", forbidden_lookup)
    response = await isolated_controller.recall_all_coding_preferences_http.__wrapped__(
        _request(body, method="GET", query_string=query_string)
    )
    assert response.status_code == 400
    assert loads(response.body)["code"] == BizCode.INVALID_PARAMETER


@pytest.mark.asyncio
async def test_full_http_dependency_failure_is_503_without_partial_data(monkeypatch, isolated_controller):
    async def caller(request):
        return ("workspace", "user", "config"), None

    async def execute(**kwargs):
        raise RuntimeError("sensitive preference body")

    monkeypatch.setattr(isolated_controller, "_resolve_caller", caller)
    monkeypatch.setattr(isolated_controller, "execute_full_preference_recall", execute)
    response = await isolated_controller.recall_all_coding_preferences_http.__wrapped__(
        _request(b"", method="GET")
    )
    assert response.status_code == 503
    assert loads(response.body)["code"] == BizCode.SERVICE_UNAVAILABLE
    assert "sensitive preference body" not in response.body.decode()
    assert "preferences" not in response.body.decode()


@pytest.mark.asyncio
async def test_on_demand_http_retains_request_contract_and_400_status(monkeypatch, isolated_controller):
    async def caller(request):
        return ("workspace", "user", "config"), None

    async def execute(**kwargs):
        assert kwargs["request_payload"].current_user_message == "继续"
        return []

    monkeypatch.setattr(isolated_controller, "_resolve_caller", caller)
    monkeypatch.setattr(isolated_controller, "execute_preference_recall", execute)
    valid = await isolated_controller.recall_coding_preferences_http.__wrapped__(
        _request('{"current_user_message":"继续"}'.encode())
    )
    invalid = await isolated_controller.recall_coding_preferences_http.__wrapped__(
        _request('{"current_user_message":"继续","mode":"full"}'.encode())
    )
    assert valid.status_code == 200
    assert loads(valid.body)["data"] == {"preferences": [], "count": 0}
    assert invalid.status_code == 400
    assert loads(invalid.body)["code"] == BizCode.INVALID_PARAMETER


@pytest.mark.asyncio
async def test_full_http_resolves_end_user_only_with_authenticated_workspace(monkeypatch, isolated_controller):
    @asynccontextmanager
    async def db_context():
        yield object()

    class Users:
        def __init__(self, db):
            pass

        async def get_end_user_by_other_id_async(self, workspace_id, other_id):
            assert (workspace_id, other_id) == ("authenticated-workspace", "external-user")
            return None

    monkeypatch.setattr(isolated_controller, "get_current_api_key_auth", lambda: SimpleNamespace(workspace_id="authenticated-workspace"))
    monkeypatch.setattr(isolated_controller, "get_async_db_context", db_context)
    monkeypatch.setattr(isolated_controller, "EndUserRepository", Users)
    response = await isolated_controller.recall_all_coding_preferences_http.__wrapped__(
        _request(b"", method="GET")
    )
    assert response.status_code == 403
    assert loads(response.body)["code"] == BizCode.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_resolved_end_user_id_is_read_before_session_closes(monkeypatch, isolated_controller):
    session_open = False

    @asynccontextmanager
    async def db_context():
        nonlocal session_open
        session_open = True
        try:
            yield object()
        finally:
            session_open = False

    class EndUser:
        @property
        def id(self):
            if not session_open:
                raise RuntimeError("Instance is not bound to a Session")
            return "resolved-user-id"

    class Users:
        def __init__(self, db):
            pass

        async def get_end_user_by_other_id_async(self, workspace_id, other_id):
            return EndUser()

    class Configs:
        def __init__(self, db):
            pass

        async def get_workspace_active_config_id_async(self, workspace_id):
            return "config-id"

    monkeypatch.setattr(isolated_controller, "get_current_api_key_auth", lambda: SimpleNamespace(workspace_id="workspace"))
    monkeypatch.setattr(isolated_controller, "get_async_db_context", db_context)
    monkeypatch.setattr(isolated_controller, "EndUserRepository", Users)
    monkeypatch.setattr(isolated_controller, "MemoryConfigService", Configs)
    caller, error = await isolated_controller._resolve_caller(_request(b""))
    assert error is None
    assert caller == ("workspace", "resolved-user-id", "config-id")
    assert session_open is False


def test_missing_api_key_keeps_existing_http_mapping():
    assert HTTP_MAPPING[BizCode.API_KEY_NOT_FOUND] == 400
