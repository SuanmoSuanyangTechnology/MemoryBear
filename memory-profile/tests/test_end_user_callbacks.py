"""Tests for the end-user callbacks: domain-info projection and info update.

The projection and the update rules are pure functions or use-case calls with
faked repositories, so these tests pin the host semantics without a database.
The route tests only assert registration and the principal requirement.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from src.app import create_app
from src.config import ProfileSettings
from src.repositories.end_user_aliases_mutation import AliasUpdateResult
from src.services.end_user_domain import (
    DOMAIN_VALUES_LIMIT,
    project_domains,
)
from src.services.end_user_info import (
    EndUserInfoInvalidIdError,
    EndUserInfoNotFoundError,
    EndUserInfoUpdateService,
    is_user_placeholder_name,
    normalize_aliases,
    normalize_other_name,
)

_WORKSPACE = UUID(int=3)
_OTHER_WORKSPACE = UUID(int=4)
_END_USER = UUID(int=100)
_SCENE_ID = str(UUID(int=200))


def _meta(domains: dict[str, list[str]]) -> dict[str, Any]:
    return {"identity_background": [], "relationships": [], "domain_specific_info": domains}


# ── domain projection ───────────────────────────────────────────


def test_projection_groups_entries_by_scene_and_field() -> None:
    meta = _meta({_SCENE_ID: ["职业 || 工程师", "兴趣 || 咖啡"]})

    result = project_domains(meta, {_SCENE_ID: "工作"})

    assert set(result) == {"工作"}
    assert result["工作"]["职业"]["values"] == ["工程师"]
    assert result["工作"]["兴趣"]["values"] == ["咖啡"]
    # No enterprise field labels: the field key is the label, like the host.
    assert result["工作"]["职业"]["field_label"] == "职业"


def test_projection_drops_unknown_scene_ids_and_keeps_legacy_keys() -> None:
    meta = _meta(
        {
            str(UUID(int=201)): ["职业 || 保密"],  # resolves to no name
            "旧场景": ["职业 || 保留"],
        }
    )

    result = project_domains(meta, {})

    assert "旧场景" in result
    assert len(result) == 1


def test_projection_merges_same_named_scenes_and_dedupes_values() -> None:
    first, second = str(UUID(int=202)), str(UUID(int=203))
    meta = _meta({first: ["职业 || 工程师"], second: ["职业 || 工程师", "职业 || 教师"]})

    result = project_domains(meta, {first: "工作", second: "工作"})

    assert result["工作"]["职业"]["values"] == ["工程师", "教师"]


def test_projection_caps_values_per_field() -> None:
    items = [f"职业 || 值{i}" for i in range(DOMAIN_VALUES_LIMIT + 5)]
    result = project_domains(_meta({_SCENE_ID: items}), {_SCENE_ID: "工作"})

    assert len(result["工作"]["职业"]["values"]) == DOMAIN_VALUES_LIMIT


def test_projection_skips_level3_entries_and_malformed_items() -> None:
    meta = _meta({_SCENE_ID: ["职业 | 子类 | 值", "没有分隔符", 42]})

    result = project_domains(meta, {_SCENE_ID: "工作"})

    # Host parity: the scene stays visible but contributes no field.
    assert result == {"工作": {}}


def test_projection_of_missing_domains_is_empty() -> None:
    assert project_domains({}, {}) == {}
    assert project_domains(None, {}) == {}


# ── name and alias normalisation ────────────────────────────────


def test_placeholder_names_are_recognised_case_insensitively() -> None:
    for name in ("用户", "我", "User", "I", " user "):
        assert is_user_placeholder_name(name)

    assert not is_user_placeholder_name("张三")


def test_other_name_skips_blank_and_placeholder_values() -> None:
    assert normalize_other_name("  张三  ") == "张三"
    assert normalize_other_name("   ") is None
    assert normalize_other_name("我") is None
    assert normalize_other_name(None) is None
    assert normalize_other_name(42) is None


def test_aliases_strip_drop_placeholders_and_dedupe() -> None:
    assert normalize_aliases(["  张三 ", "我", "", "zhang", "ZHANG", 3]) == [
        "张三",
        "zhang",
    ]
    assert normalize_aliases(None) == []


# ── info update use case ────────────────────────────────────────


class _Reads:
    def __init__(self, *, row: dict[str, Any] | None, workspace_id: UUID = _WORKSPACE):
        self.row = row
        self.workspace_id = workspace_id
        self.display: dict[str, Any] | None = {
            "id": UUID(int=500),
            "end_user_id": _END_USER,
            "other_name": "旧名",
            "aliases": ["旧别名"],
            "meta_data_new": {},
            "created_at": None,
            "updated_at": None,
        }

    async def get_active_or_merged_end_user(self, end_user_id: UUID):
        if self.row is None:
            return None
        return {"id": end_user_id, "workspace_id": self.workspace_id}

    async def get_display_row(self, end_user_id: UUID):
        return self.display


class _Mutations:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update_info(self, end_user_id: UUID, **kwargs: Any):
        self.calls.append(kwargs)
        return {
            "id": UUID(int=500),
            "end_user_id": end_user_id,
            "other_name": kwargs.get("other_name"),
            "aliases": kwargs.get("aliases"),
            "meta_data": {},
            "created_at": None,
            "updated_at": None,
        }


def _service(reads: _Reads, mutations: _Mutations, writer=None):
    return EndUserInfoUpdateService(
        reads=reads, mutations=mutations, alias_writer=writer
    )


def test_update_writes_alias_change_and_reports_the_graph_sync() -> None:
    calls: list[tuple[str, list[str]]] = []

    async def writer(end_user_id: str, aliases: list[str]):
        calls.append((end_user_id, aliases))
        return AliasUpdateResult(affected_count=2, outbox_events=2)

    service = _service(_Reads(row={}), _Mutations(), writer)

    data = asyncio.run(
        service.update(
            workspace_id=_WORKSPACE,
            end_user_id=str(_END_USER),
            update_data={"aliases": ["新别名", "我"]},
        )
    )

    assert calls == [(str(_END_USER), ["新别名"])]
    assert data["neo4j_sync_status"] == "success"


def test_update_skips_the_graph_when_aliases_are_unchanged() -> None:
    calls: list[Any] = []

    async def writer(end_user_id: str, aliases: list[str]):
        calls.append((end_user_id, aliases))
        return AliasUpdateResult()

    service = _service(_Reads(row={}), _Mutations(), writer)

    data = asyncio.run(
        service.update(
            workspace_id=_WORKSPACE,
            end_user_id=str(_END_USER),
            update_data={"aliases": ["旧别名"]},
        )
    )

    assert calls == []
    assert data["neo4j_sync_status"] == "skipped"


def test_update_ignores_meta_data_and_placeholder_names() -> None:
    mutations = _Mutations()
    service = _service(_Reads(row={}), mutations)

    asyncio.run(
        service.update(
            workspace_id=_WORKSPACE,
            end_user_id=str(_END_USER),
            update_data={"meta_data": {"x": 1}, "other_name": "我"},
        )
    )

    assert mutations.calls == [
        {
            "other_name": None,
            "aliases": None,
            "update_other_name": False,
            "update_aliases": False,
        }
    ]


def test_update_rejects_a_foreign_or_unknown_user() -> None:
    service = _service(_Reads(row=None), _Mutations())

    try:
        asyncio.run(
            service.update(
                workspace_id=_WORKSPACE,
                end_user_id=str(_END_USER),
                update_data={"other_name": "张三"},
            )
        )
    except EndUserInfoNotFoundError:
        pass
    else:  # pragma: no cover - the assertion is the point
        raise AssertionError("a merged/foreign user must be rejected")

    foreign = _service(_Reads(row={}, workspace_id=_OTHER_WORKSPACE), _Mutations())
    try:
        asyncio.run(
            foreign.update(
                workspace_id=_WORKSPACE,
                end_user_id=str(_END_USER),
                update_data={"other_name": "张三"},
            )
        )
    except EndUserInfoNotFoundError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a user of another workspace must be rejected")


def test_update_rejects_a_malformed_id() -> None:
    service = _service(_Reads(row={}), _Mutations())

    try:
        asyncio.run(
            service.update(
                workspace_id=_WORKSPACE,
                end_user_id="not-a-uuid",
                update_data={"other_name": "张三"},
            )
        )
    except EndUserInfoInvalidIdError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a malformed id must be rejected")


def test_update_reports_a_failed_graph_sync_without_raising() -> None:
    async def writer(end_user_id: str, aliases: list[str]):
        raise RuntimeError("neo4j down")

    service = _service(_Reads(row={}), _Mutations(), writer)

    data = asyncio.run(
        service.update(
            workspace_id=_WORKSPACE,
            end_user_id=str(_END_USER),
            update_data={"aliases": ["新别名"]},
        )
    )

    # PostgreSQL already committed; the failure is reported, not raised.
    assert data["neo4j_sync_status"] == "failed"


# ── routes ──────────────────────────────────────────────────────


def _app() -> Any:
    return create_app(settings=ProfileSettings.model_validate({}))


def test_end_user_routes_are_registered() -> None:
    paths = {
        "/internal/v1" + context.route.path
        for context in iter_route_contexts(_app().routes)
    }

    assert "/internal/v1/end-user/domain-profile" in paths
    assert "/internal/v1/end-user/info-update" in paths


def test_end_user_routes_require_the_internal_principal() -> None:
    with TestClient(_app()) as client:
        response = client.post(
            "/internal/v1/end-user/domain-profile",
            json={"end_user_id": str(_END_USER)},
        )

    assert response.status_code == 401
    assert response.json()["code"] == 2001
