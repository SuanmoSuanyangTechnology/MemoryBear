"""Authorization regressions for the emotion reply endpoint and service."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import UUID

import pytest
from auth_sdk.schema import UserContext
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from src.common.errors import BizCode, ProfileError
from src.error_handlers import register_error_handlers
from src.middleware.auth import MemoryAuthConfig, MemoryAuthMiddleware, MemoryPrincipal
from src.modules.emotion_reply import routes
from src.schemas.emotion_reply import EmotionPolicy
from src.services.emotion_reply import EmotionReplyService

_KEY, _TENANT, _WORKSPACE, _USER = (UUID(int=i) for i in range(1, 5))
_TRUSTED = {
    "x-source": "ex_api",
    "x-api-key-id": str(_KEY),
    "x-tenant-id": str(_TENANT),
    "x-workspace-id": str(_WORKSPACE),
}
_BEARER = {"authorization": "Bearer test-token"}
_CLAIMS = UserContext(
    user_id=str(_USER),
    tenant_id=str(_TENANT),
    workspace_id=str(_WORKSPACE),
    auth_type="api_key",
    scopes=("memory",),
    api_key_id=str(_KEY),
)


@pytest.fixture
def binding():
    return AsyncMock(return_value=_KEY)


@pytest.fixture
def deps(binding):
    events = []

    @asynccontextmanager
    async def session():
        try:
            yield SimpleNamespace(scalar=binding)
        finally:
            events.append("session closed")

    def predict(message):
        events.append("predict")
        return "joy", 0.9

    return SimpleNamespace(
        postgres=SimpleNamespace(session=session),
        predict=AsyncMock(side_effect=predict),
        load_policy=AsyncMock(return_value=EmotionPolicy(tone="calm", do=[], avoid=[])),
        events=events,
    )


def _service(deps, *, policy_enabled=True):
    return EmotionReplyService(
        deps.predict, deps.load_policy, postgres=deps.postgres, policy_enabled=policy_enabled
    )


def _post(deps, headers, *, mode="direct", gateway=None):
    app = FastAPI()
    register_error_handlers(app)
    app.state.runtime = SimpleNamespace(emotion_reply=_service(deps))
    app.add_middleware(
        MemoryAuthMiddleware,
        config=MemoryAuthConfig(auth_mode=mode),
        gateway_auth=gateway,
    )
    app.include_router(routes.router, prefix="/internal/v1")
    with TestClient(app) as client:
        return client.post(
            "/internal/v1/memory/emotion-reply", headers=headers, json={"message": "hello"}
        )


@pytest.mark.parametrize(
    ("mode", "claims", "headers"),
    [
        pytest.param("direct", None, _TRUSTED, id="direct"),
        pytest.param("gateway", None, _TRUSTED, id="gateway-transition"),
        pytest.param("gateway", _CLAIMS, _BEARER, id="verified-gateway"),
    ],
)
@pytest.mark.parametrize("bound", [True, False], ids=["bound", "wrong-binding"])
def test_service_key_requires_current_tenant_and_workspace_binding(
    mode, claims, headers, bound, binding, deps
):
    gateway = SimpleNamespace(authenticate=AsyncMock(return_value=claims))
    binding.return_value = _KEY if bound else None

    response = _post(deps, headers, mode=mode, gateway=gateway)

    if mode == "gateway":
        gateway.authenticate.assert_awaited_once()
    binding.assert_awaited_once()
    if bound:
        assert response.status_code == 200
        assert response.json()["data"]["tone"] == "calm"
        assert deps.events == ["session closed", "predict"]
        deps.predict.assert_awaited_once_with("hello")
        deps.load_policy.assert_awaited_once_with("joy", ANY, _TENANT, _WORKSPACE)
    else:
        assert response.status_code == 403
        assert response.json()["code"] == BizCode.API_KEY_INVALID
        deps.predict.assert_not_awaited()
        deps.load_policy.assert_not_awaited()


@pytest.mark.parametrize("status", [401, 403], ids=["invalid-token", "acl-denied"])
def test_gateway_auth_failure_stops_before_key_lookup(status, binding, deps):
    gateway = SimpleNamespace(authenticate=AsyncMock(side_effect=HTTPException(status)))

    response = _post(deps, _TRUSTED | _BEARER, mode="gateway", gateway=gateway)

    assert response.status_code == status
    gateway.authenticate.assert_awaited_once()
    binding.assert_not_awaited()
    deps.predict.assert_not_awaited()
    deps.load_policy.assert_not_awaited()


@pytest.mark.parametrize(
    ("claims", "code"),
    [
        pytest.param(replace(_CLAIMS, scopes=()), BizCode.WORKSPACE_ACCESS_DENIED, id="no-scope"),
        pytest.param(
            replace(_CLAIMS, auth_type=None, api_key_id=None, scopes=()),
            BizCode.API_KEY_INVALID,
            id="internal-user",
        ),
    ],
)
def test_gateway_rejects_ineligible_principal_even_with_service_headers(
    claims, code, binding, deps
):
    gateway = SimpleNamespace(authenticate=AsyncMock(return_value=claims))

    response = _post(deps, _TRUSTED | _BEARER, mode="gateway", gateway=gateway)

    assert response.status_code == 403
    assert response.json()["code"] == code
    binding.assert_not_awaited()
    deps.predict.assert_not_awaited()
    deps.load_policy.assert_not_awaited()


def test_binding_lookup_failure_returns_service_unavailable(binding, deps):
    binding.side_effect = SQLAlchemyError("database unavailable")

    response = _post(deps, _TRUSTED)

    assert response.status_code == 503
    assert response.json()["code"] == BizCode.SERVICE_UNAVAILABLE
    deps.predict.assert_not_awaited()
    deps.load_policy.assert_not_awaited()


@pytest.mark.parametrize(
    ("bound", "status", "code"),
    [(False, 403, BizCode.API_KEY_INVALID), (True, 503, BizCode.SERVICE_UNAVAILABLE)],
)
async def test_service_checks_binding_before_disabled_policy(bound, status, code, binding, deps):
    binding.return_value = _KEY if bound else None
    context = MemoryPrincipal(
        actor_id=_KEY,
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        source="ex_api",
        actor_kind="apikey",
        trace_id="service-call",
    )
    service = _service(deps, policy_enabled=False)

    with pytest.raises(ProfileError) as error:
        await service.resolve(context, "hello")

    assert error.value.status_code == status
    assert error.value.code == code
    binding.assert_awaited_once()
    deps.predict.assert_not_awaited()
    deps.load_policy.assert_not_awaited()
