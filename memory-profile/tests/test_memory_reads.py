import ast
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.dialects import postgresql

from src.common.errors import BizCode, ProfileError
from src.repositories.display import MemoryDisplayRepository
from src.repositories.implicit_memory import ImplicitMemoryRepository
from src.repositories.perceptual import MemoryPerceptualRepository
from src.schemas.display import AllItem, RetrievedItem, WrittenItem
from src.services.display.query import MemoryDisplayService
from src.services.end_user import EndUserService
from src.services.implicit_memory import ImplicitMemoryService, _profile_timestamp
from src.services.perceptual import PerceptualMemoryService


def users(allowed=True):
    repository = SimpleNamespace(user_exists_in_workspace=AsyncMock(return_value=allowed))
    return EndUserService(repository), repository


def display_repository():
    return SimpleNamespace(
        query_written_paginated=AsyncMock(return_value=([], 0)),
        query_retrieved_paginated=AsyncMock(return_value=([], 0)),
        query_engine_groups_paginated=AsyncMock(return_value=([], 0)),
    )


@pytest.mark.parametrize("include_engines", [True, False])
async def test_all_checks_user_once_and_keeps_limits(include_engines):
    end_users, user_repository = users()
    repository = display_repository()
    service = MemoryDisplayService(repository=repository, end_users=end_users)
    workspace_id, end_user_id = uuid4(), uuid4()
    assert await service.query_all(
        workspace_id=workspace_id,
        end_user_id=end_user_id,
        timezone="Asia/Shanghai",
        language="en",
        page=1,
        pagesize=10,
        include_engines=include_engines,
    ) == ([], 0)
    user_repository.user_exists_in_workspace.assert_awaited_once_with(end_user_id, workspace_id)
    assert repository.query_written_paginated.await_args.kwargs["pagesize"] == 1000
    assert repository.query_engine_groups_paginated.await_count == int(include_engines)


@pytest.mark.parametrize("kind", ["display", "perceptual", "implicit"])
async def test_denied_users_never_reach_data_repository(kind):
    end_users, _ = users(False)
    workspace_id, end_user_id = uuid4(), uuid4()
    repository = display_repository()
    repository.count_by_type = AsyncMock()
    repository.get_profile = AsyncMock()
    if kind == "display":
        service = MemoryDisplayService(repository=repository, end_users=end_users)
        request = service.query_written(
            workspace_id=workspace_id, end_user_id=end_user_id, page=1, pagesize=10
        )
    elif kind == "perceptual":
        service = PerceptualMemoryService(repository=repository, end_users=end_users)
        request = service.count(workspace_id=workspace_id, end_user_id=end_user_id)
    else:
        service = ImplicitMemoryService(repository=repository, end_users=end_users)
        request = service.check_data(workspace_id=workspace_id, end_user_id=end_user_id)
    with pytest.raises(ProfileError) as failure:
        await request
    assert failure.value.code == BizCode.USER_NOT_FOUND
    assert failure.value.status_code == 404
    repository.query_written_paginated.assert_not_awaited()
    repository.count_by_type.assert_not_awaited()
    repository.get_profile.assert_not_awaited()


async def test_all_uses_models_and_keeps_source_order_and_total():
    end_users, _ = users()
    repository = display_repository()
    uid = uuid4()
    record = dict(
        id=uuid4(),
        end_user_id=uid,
        memory_id=None,
        memory_type=None,
        name=None,
        content="写入原文",
        occurred_at=datetime(2026, 1, 1),
    )
    repository.query_written_paginated.return_value = ([record], 8)
    retrieved = dict(
        id=uuid4(),
        end_user_id=uid,
        search_mode=None,
        query=None,
        content="检索原文",
        occurred_at=datetime(2026, 1, 2),
    )
    repository.query_retrieved_paginated.return_value = ([retrieved], 12)
    service = MemoryDisplayService(repository=repository, end_users=end_users)
    items, total = await service.query_all(
        workspace_id=uuid4(),
        end_user_id=uid,
        timezone="UTC",
        language="zh",
        page=1,
        pagesize=10,
        include_engines=False,
    )
    assert total == 20
    assert [item.source for item in items] == ["retrieved", "written"]
    assert "name" not in items[0].model_dump()
    assert items[1].name is None
    assert items[0].query == ""


def test_written_requires_name_key_but_allows_null():
    row = dict(
        id="1",
        end_user_id="2",
        memory_id=None,
        memory_type=None,
        name=None,
        content="原文",
        occurred_at=None,
    )
    assert WrittenItem(**row).name is None
    row["title"] = row.pop("name")
    with pytest.raises(ValidationError):
        WrittenItem(**row)
    retrieved = RetrievedItem(
        id="1", end_user_id="2", search_mode=None, query="", content="", occurred_at=0
    )
    assert "name" not in retrieved.model_dump()
    with pytest.raises(ValidationError):
        TypeAdapter(AllItem).validate_python({"source": "written", **retrieved.model_dump()})


@pytest.mark.parametrize(
    "value", [1767225600000, "2026-01-01T00:00:00Z", "2026-01-01T08:00:00+08:00"]
)
def test_numeric_and_iso_timestamps_represent_the_same_utc_instant(value):
    assert _profile_timestamp(value) == datetime(2026, 1, 1)


@pytest.mark.parametrize("value", ["invalid", float("nan"), float("inf"), None])
def test_invalid_timestamps_do_not_crash(value):
    assert _profile_timestamp(value) is None


async def test_implicit_sections_remain_extensible_and_date_filter_matches_formats():
    end_users, _ = users()
    repository = SimpleNamespace(
        get_profile=AsyncMock(
            return_value={
                "preferences": [
                    {"confidence_score": 0.8, "created_at": 1767225600000, "extra": {"custom": 1}},
                    {
                        "confidence_score": 0.8,
                        "created_at": "2026-01-01T08:00:00+08:00",
                        "extra": 2,
                    },
                ],
                "portrait": {"custom_axis": [1, 2]},
            }
        )
    )
    service = ImplicitMemoryService(repository=repository, end_users=end_users)
    preferences = await service.preferences(
        end_user_id=uuid4(),
        workspace_id=uuid4(),
        confidence_threshold=0.5,
        tag_category=None,
        start_date=datetime(2025, 12, 31),
        end_date=datetime(2026, 1, 2),
    )
    assert len(preferences) == 2
    assert preferences[0]["extra"] == {"custom": 1}


class CapturePostgres:
    def __init__(self):
        self.statements = []

    @asynccontextmanager
    async def session(self):
        async def execute(statement):
            self.statements.append(statement)
            return SimpleNamespace(scalar=lambda: 0, all=list)

        async def scalar(statement):
            self.statements.append(statement)
            return None

        yield SimpleNamespace(execute=execute, scalar=scalar)


@pytest.mark.parametrize("kind", ["display", "perceptual", "implicit"])
async def test_data_sql_reuses_active_user_and_workspace_scope(kind):
    postgres = CapturePostgres()
    uid, wid = uuid4(), uuid4()
    if kind == "display":
        await MemoryDisplayRepository(postgres).query_written_paginated(wid, 1, 10, uid)
    elif kind == "perceptual":
        await MemoryPerceptualRepository(postgres).count_by_type(uid, wid)
    else:
        await ImplicitMemoryRepository(postgres).get_profile(uid, wid)
    sql = str(postgres.statements[0].compile(dialect=postgresql.dialect()))
    assert "workspaces.is_active IS true" in sql
    assert "end_users.is_active IS true" in sql
    assert "end_users.workspace_id =" in sql
    if kind == "display":
        assert "memory_display_records.workspace_id =" not in sql  # legacy NULL is allowed


async def test_workspace_display_still_restricts_workspace_and_activity():
    postgres = CapturePostgres()
    await MemoryDisplayRepository(postgres).query_written_paginated(uuid4(), 1, 10)
    sql = str(postgres.statements[0].compile(dialect=postgresql.dialect()))
    assert "memory_display_records.workspace_id =" in sql
    assert "workspaces.is_active IS true" in sql


@pytest.mark.parametrize(
    "value", [1767225600000, "2026-01-01T00:00:00Z", "2026-01-01T08:00:00+08:00", None, "bad"]
)
def test_preference_timestamp_api_fallback_matches_profile(value):
    path = Path(__file__).resolve().parents[2] / "api/app/controllers/implicit_memory_controller.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_profile_timestamp"
    )
    namespace = dict(datetime=datetime, UTC=UTC)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace["_profile_timestamp"](value) == _profile_timestamp(value)


def test_importing_perceptual_type_does_not_load_routes():
    service_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from src.schemas.perceptual import PerceptualType; "
            "assert PerceptualType.CONVERSATION == 4; "
            "assert not any(n == 'src.modules' or n.startswith('src.modules.') "
            "for n in sys.modules)",
        ],
        cwd=service_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
