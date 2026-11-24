"""Preview contract, isolation and remote-model tests; no external infrastructure."""

import ast
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.error_handlers import register_error_handlers
from src.i18n import LanguageMiddleware, configure_i18n
from src.middleware.auth import MemoryAuthConfig, MemoryAuthMiddleware
from src.modules.reflection.routes import router
from src.repositories.memory_config import MemoryConfigReadRepository
from src.schemas.reflection_trial import (
    ConflictResultSchema,
    ReflectionTrialConfig,
    ReflectionTrialData,
    ReflectionTrialFailure,
    ReflexionResultSchema,
)
from src.services.memory_config.errors import ConfigNotFoundError
from src.services.memory_config_trial.reflection import ReflectionTrialService, run_preview
from src.services.model_config import ModelConfigurationError

CORE = Path(__file__).resolve().parents[2]
CONFIG_ID, WORKSPACE_ID, TENANT_ID, MODEL_ID = (uuid4() for _ in range(4))
CONFLICT = {
    "data": [{"id": "1", "description": "冲突", "created_at": "2024-01-01T00:00:00Z"}],
    "conflict": True,
    "memory_verify": {"has_privacy": True, "privacy_types": ["身份"], "summary": "隐私"},
    "quality_assessment": {"score": 80, "summary": "质量"},
}
RESOLVED = {
    "results": [{"conflict": CONFLICT, "reflexion": {"reason": "原因", "solution": "解决"}}]
}


class FakeModel:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    async def call_structured(self, messages, schema):
        self.calls.append((messages, schema))
        if len(self.calls) == self.fail_at:
            raise RuntimeError("model unavailable")
        if schema.__name__ == "TranslationResponse":
            return schema(data="translated")
        data = CONFLICT if schema.__name__ == "ConflictResultSchema" else RESOLVED
        return schema.model_validate(copy.deepcopy(data))


@pytest.fixture(autouse=True)
def language():
    configure_i18n()


def config(**overrides):
    return ReflectionTrialConfig.model_validate(
        {
            "baseline": "FACT",
            "reflexion_range": "all",
            "memory_verify": True,
            "quality_assessment": True,
            **overrides,
        }
    )


def service():
    result = ReflectionTrialService(
        None, SimpleNamespace(chat_model=AsyncMock(return_value=FakeModel()))
    )
    result.configs = SimpleNamespace(
        get_reflection_trial=AsyncMock(
            return_value={
                **config().model_dump(),
                "llm": str(MODEL_ID),
                "reflection_model_id": str(uuid4()),
                "enable_self_reflexion": False,
            }
        )
    )
    result.models = SimpleNamespace(
        get_models=AsyncMock(return_value={MODEL_ID: {"is_active": True}})
    )
    return result


@pytest.mark.parametrize("baseline", ["TIME", "FACT", "HYBRID"])
@pytest.mark.parametrize(
    "memory_verify,quality_assessment", [(True, True), (False, False), (True, False)]
)
async def test_preview_flags_and_baselines(baseline, memory_verify, quality_assessment):
    model = FakeModel()
    result = await run_preview(
        config(
            baseline=baseline, memory_verify=memory_verify, quality_assessment=quality_assessment
        ),
        model,
        locale="zh",
    )
    assert isinstance(result, ReflectionTrialData)
    assert result.baseline == baseline
    assert bool(result.memory_verifies) == memory_verify
    assert bool(result.quality_assessments) == quality_assessment
    assert result.reflexion_data[0].solution == "解决"
    assert len(model.calls) == 2
    assert "'created_at':" not in model.calls[1][0][0]["content"].split("原始句子")[0]


async def test_english_keeps_baseline_and_translates_text():
    result = await run_preview(config(), FakeModel(), locale="en")
    assert result.baseline == "FACT"
    assert result.source_data == "translated"
    assert result.memory_verifies[0].privacy_types == ["translated"]
    assert result.memory_verifies[0].has_privacy is True
    assert result.quality_assessments[0].score == 80
    assert result.reflexion_data[0].solution == "translated"


@pytest.mark.parametrize("fail_at", [1, 2])
async def test_model_failure_is_legacy_data_with_nonnegative_duration(fail_at):
    result = await run_preview(config(), FakeModel(fail_at), locale="zh")
    assert isinstance(result, ReflectionTrialFailure)
    assert result.success is False
    assert result.message == "反思失败，未解决冲突"
    assert result.execution_time >= 0
    assert result.memories_updated == 0


async def test_null_optional_assessments_translate_without_crashing():
    model = FakeModel()
    original = model.call_structured

    async def call(messages, schema):
        value = await original(messages, schema)
        if schema is ConflictResultSchema:
            value.memory_verify = None
            value.quality_assessment = None
        return value

    model.call_structured = call
    result = await run_preview(config(), model, locale="en")
    assert result.memory_verifies == [None]
    assert result.quality_assessments == [None]


@pytest.mark.parametrize("identifier", [CONFIG_ID, 42])
async def test_trial_uses_workspace_llm_and_propagates_identity(identifier):
    trial = service()
    await trial.run(identifier, WORKSPACE_ID, TENANT_ID, locale="zh")
    trial.configs.get_reflection_trial.assert_awaited_once_with(identifier, WORKSPACE_ID, TENANT_ID)
    trial.models.get_models.assert_awaited_once_with([MODEL_ID], TENANT_ID)
    trial.model_service.chat_model.assert_awaited_once_with(MODEL_ID, TENANT_ID)


async def test_denied_config_never_reaches_model():
    trial = service()
    trial.configs.get_reflection_trial.return_value = None
    with pytest.raises(ConfigNotFoundError):
        await trial.run(CONFIG_ID, WORKSPACE_ID, TENANT_ID, locale="zh")
    trial.models.get_models.assert_not_called()
    trial.model_service.chat_model.assert_not_called()


@pytest.mark.parametrize("model_rows", [{}, {MODEL_ID: {"is_active": False}}])
async def test_missing_foreign_or_disabled_model_is_rejected(model_rows):
    trial = service()
    trial.models.get_models.return_value = model_rows
    with pytest.raises(ModelConfigurationError):
        await trial.run(CONFIG_ID, WORKSPACE_ID, TENANT_ID, locale="zh")
    trial.model_service.chat_model.assert_not_called()


@pytest.mark.parametrize("identifier", [CONFIG_ID, 42])
async def test_repository_scopes_both_id_types_before_returning_data(identifier):
    statements = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: None))

    repository = MemoryConfigReadRepository(SimpleNamespace(session=Session))
    assert await repository.get_reflection_trial(identifier, WORKSPACE_ID, TENANT_ID) is None
    compiled = statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "memory_config.workspace_id =" in sql
    assert "workspaces.tenant_id =" in sql
    assert "workspaces.is_active IS true" in sql
    assert WORKSPACE_ID in compiled.params.values()
    assert TENANT_ID in compiled.params.values()
    assert identifier in compiled.params.values()
    assert ("config_id_old =" in sql) == isinstance(identifier, int)


@pytest.fixture
def client(monkeypatch):
    trial = service()
    monkeypatch.setattr(
        "src.modules.reflection.handler.ReflectionTrialService", lambda *args: trial
    )
    app = FastAPI()
    app.state.runtime = SimpleNamespace(postgres=None, model_service=None)
    app.include_router(router, prefix="/internal/v1")
    app.add_middleware(MemoryAuthMiddleware, config=MemoryAuthConfig(auth_mode="direct"))
    app.add_middleware(LanguageMiddleware)
    register_error_handlers(app)
    return TestClient(app), trial


def headers():
    return {
        "x-user-id": str(uuid4()),
        "x-workspace-id": str(WORKSPACE_ID),
        "x-tenant-id": str(TENANT_ID),
        "x-source": "in_api",
    }


@pytest.mark.parametrize("identifier", [str(CONFIG_ID), "42"])
def test_http_contract(client, identifier):
    http, _ = client
    response = http.get(
        "/internal/v1/memory/reflection/run", params={"config_id": identifier}, headers=headers()
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "反思试运行"
    assert set(response.json()["data"]) == {
        "baseline",
        "source_data",
        "memory_verifies",
        "quality_assessments",
        "reflexion_data",
    }


def test_http_missing_identity(client):
    http, trial = client
    assert (
        http.get(
            "/internal/v1/memory/reflection/run", params={"config_id": str(CONFIG_ID)}
        ).status_code
        == 401
    )
    trial.configs.get_reflection_trial.assert_not_called()


def test_http_api_key_cannot_run_trial(client):
    http, trial = client
    request_headers = headers()
    request_headers["x-source"] = "ex_api"
    request_headers["x-api-key-id"] = request_headers.pop("x-user-id")
    response = http.get(
        "/internal/v1/memory/reflection/run",
        params={"config_id": str(CONFIG_ID)},
        headers=request_headers,
    )
    assert response.status_code == 403
    trial.configs.get_reflection_trial.assert_not_called()


def test_http_missing_config_preserves_404(client):
    http, trial = client
    trial.configs.get_reflection_trial.return_value = None
    response = http.get(
        "/internal/v1/memory/reflection/run",
        params={"config_id": str(CONFIG_ID)},
        headers=headers(),
    )
    assert response.status_code == 404
    assert response.json()["code"] == 404
    assert response.json()["error"] == f"未找到config_id为 {CONFIG_ID} 的配置"


def test_http_model_configuration_failure(client):
    http, trial = client
    trial.models.get_models.return_value = {}
    response = http.get(
        "/internal/v1/memory/reflection/run",
        params={"config_id": str(CONFIG_ID)},
        headers=headers(),
    )
    assert response.status_code == 500
    assert response.json()["code"] == 10001


def test_http_validation(client):
    http, trial = client
    response = http.get(
        "/internal/v1/memory/reflection/run", params={"config_id": "bad-id"}, headers=headers()
    )
    assert response.status_code == 422
    trial.configs.get_reflection_trial.assert_not_called()


async def test_legacy_chinese_output_and_prompts_match():
    # Load only the old preview methods so parity does not require API infrastructure.
    import asyncio
    import json
    import logging
    import os
    import time

    from pydantic import BaseModel

    path = CORE / "api/app/core/memory/storage_services/reflection_engine/self_reflexion.py"
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ReflectionEngine")
    names = {
        "reflection_run",
        "extract_fields_from_json",
        "_detect_conflicts",
        "_resolve_conflicts",
    }
    cls.body = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
    namespace = dict(
        asyncio=asyncio,
        json=json,
        logging=logging,
        os=os,
        time=time,
        BaseModel=BaseModel,
        __file__=str(path),
        ReflectionResult=ReflectionTrialFailure,
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[
                        ast.ImportFrom(
                            module="__future__", names=[ast.alias(name="annotations")], level=0
                        ),
                        cls,
                    ],
                    type_ignores=[],
                )
            ),
            str(path),
            "exec",
        ),
        namespace,
    )
    legacy = namespace["ReflectionEngine"]()
    legacy._lazy_init = lambda: None
    legacy.config = SimpleNamespace(**config().model_dump(), language_type="zh")
    legacy.llm_client = FakeModel()
    legacy._semaphore = asyncio.Semaphore(5)
    legacy.conflict_schema, legacy.reflexion_schema = ConflictResultSchema, ReflexionResultSchema
    spec = importlib.util.spec_from_file_location(
        "legacy_prompt", CORE / "api/app/core/memory/utils/prompt/template_render.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    legacy.render_evaluate_prompt_func = module.render_evaluate_prompt
    legacy.render_reflexion_prompt_func = module.render_reflexion_prompt
    expected = await legacy.reflection_run()
    model = FakeModel()
    actual = await run_preview(config(), model, locale="zh")
    assert actual.model_dump() == expected
    assert model.calls == legacy.llm_client.calls


@pytest.mark.parametrize("status_code", [200, 404, 500])
async def test_api_entry_forwards_query_identity_language_and_status(status_code):
    from enum import StrEnum
    from functools import wraps
    from inspect import signature
    from typing import Any
    from uuid import UUID

    from fastapi import APIRouter, Depends, Header, Request, Response

    class Source(StrEnum):
        INTERNAL = "in_api"
        APIKEY = "ex_api"

    class RawResponse:
        body = b'{"code":0,"msg":"upstream","data":{},"error":"","time":1}'
        media_type = "application/json"
        content_language = "en"

    response = RawResponse()
    response.status_code = status_code
    remote = SimpleNamespace(request=AsyncMock(return_value=response))
    actor = SimpleNamespace(id=uuid4(), current_workspace_id=WORKSPACE_ID, tenant_id=TENANT_ID)

    class ClientError(Exception):
        pass

    namespace = {
        "wraps": wraps,
        "signature": signature,
        "Any": Any,
        "UUID": UUID,
        "uuid4": uuid4,
        "Request": Request,
        "Response": Response,
        "MemoryProfileAuthSource": Source,
        "MemoryProfileRawResponse": RawResponse,
        "MemoryProfileClientError": ClientError,
        "MemoryProfileConfigurationError": ClientError,
        "_API_KEY_FACE_PREFIX": "/v1/",
        "get_current_language": AsyncMock(return_value="en"),
        "get_trace_id": lambda: "trace-123",
        "get_memory_profile_client": lambda: remote,
    }
    # Execute the actual proxy and controller entry, leaving host bootstrapping out.
    proxy = ast.parse((CORE / "api/app/integrations/profile/proxy.py").read_text())
    nodes = [
        node
        for node in proxy.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {"memory_profile_proxy", "_caller_source", "_caller_actor_id", "_resolve_identity"}
    ]
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])),
            "proxy.py",
            "exec",
        ),
        namespace,
    )
    namespace.update(
        router=APIRouter(),
        Depends=Depends,
        Header=Header,
        get_current_user_async=lambda: actor,
        CurrentUserSnapshot=SimpleNamespace,
    )
    controller = ast.parse(
        (CORE / "api/app/controllers/memory_reflection_controller.py").read_text()
    )
    entry = next(
        node
        for node in controller.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "reflection_run"
    )
    exec(compile(ast.Module(body=[entry], type_ignores=[]), "controller.py", "exec"), namespace)
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/memory/reflection/run", "headers": []}
    )
    result = await namespace["reflection_run"](
        request=request, config_id=CONFIG_ID, current_user=actor
    )
    assert result.status_code == status_code
    assert result.body == response.body
    assert result.headers["Content-Language"] == "en"
    values = remote.request.await_args.kwargs
    assert values["query"] == {"config_id": str(CONFIG_ID)}
    assert values["workspace_id"] == str(WORKSPACE_ID)
    assert values["tenant_id"] == str(TENANT_ID)
    assert values["actor_id"] == str(actor.id)
    assert values["source"] == "in_api"
    assert values["language_type"] == "en"
    assert values["trace_id"] == "trace-123"
    assert values["timeout"] == 600


def test_http_english_language(client):
    http, _ = client
    response = http.get(
        "/internal/v1/memory/reflection/run",
        params={"config_id": str(CONFIG_ID)},
        headers={**headers(), "X-Language-Type": "en"},
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "Reflection trial run"
    assert response.json()["data"]["baseline"] == "FACT"
    assert response.json()["data"]["source_data"] == "translated"
