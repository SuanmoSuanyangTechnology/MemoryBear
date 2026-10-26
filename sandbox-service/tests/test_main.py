from __future__ import annotations

import httpx
import pytest

from src import main
from src.envconf import ConfigError
from src.main import create_app_from_env

TOKEN = "unit-test-token-0123456789"
FAKE_ENV = {"SANDBOX_BACKEND": "fake", "SANDBOX_API_TOKEN": TOKEN}


def test_missing_backend_fails_at_startup():
    with pytest.raises(ConfigError, match="SANDBOX_BACKEND"):
        create_app_from_env({"SANDBOX_API_TOKEN": TOKEN})


def test_missing_token_fails_at_startup():
    with pytest.raises(ConfigError, match="SANDBOX_API_TOKEN"):
        create_app_from_env({"SANDBOX_BACKEND": "fake"})


def test_bad_scheduler_number_fails_at_startup():
    with pytest.raises(ConfigError, match="SANDBOX_CAPACITY"):
        create_app_from_env({**FAKE_ENV, "SANDBOX_CAPACITY": "lots"})


def test_enabled_egress_policy_is_loaded_during_startup(tmp_path):
    policy = tmp_path / "egress.json"
    policy.write_text('{"version":1,"allow_domains":["pypi.org"]}', encoding="utf-8")

    app = create_app_from_env(
        {
            **FAKE_ENV,
            "SANDBOX_EGRESS_ENABLED": "true",
            "SANDBOX_EGRESS_POLICY_PATH": str(policy),
        }
    )

    assert app is not None


def test_substrate_timer_shorter_than_the_scheduler_lifetime_is_refused(monkeypatch):
    class ShortLived:
        name = "short"
        native_ttl_s = 60.0

    monkeypatch.setattr(main, "build_backend", lambda env, clock: ShortLived())
    with pytest.raises(ConfigError, match="native_ttl_s"):
        create_app_from_env(FAKE_ENV)


async def test_app_serves_and_runs_its_lifespan_with_the_fake_backend():
    app = create_app_from_env(FAKE_ENV)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sandbox") as client:
            health = await client.get("/health")
            stats_no_token = await client.get("/internal/stats")
            stats = await client.get(
                "/internal/stats", headers={"Authorization": f"Bearer {TOKEN}"}
            )
    assert health.status_code == 200
    assert stats_no_token.status_code == 401
    assert stats.status_code == 200


async def test_backend_is_closed_on_shutdown(monkeypatch):
    closed: list[bool] = []

    class Closable:
        name = "closable"

        async def health(self) -> bool:
            return True

        async def aclose(self) -> None:
            closed.append(True)

        async def list_owned(self):
            return []

    monkeypatch.setattr(main, "build_backend", lambda env, clock: Closable())
    app = create_app_from_env(FAKE_ENV)
    async with app.router.lifespan_context(app):
        assert closed == []
    assert closed == [True]
