from __future__ import annotations

import httpx
import pytest

from src import bootstrap
from src.bootstrap import create_app_from_env
from src.envconf import ConfigError

TOKEN = "unit-test-token-0123456789"
# These tests are about app wiring, not egress: opt out explicitly so the default
# fail-closed egress policy does not require a policy file here.
FAKE_ENV = {
    "SANDBOX_BACKEND": "fake",
    "SANDBOX_API_TOKEN": TOKEN,
    "SANDBOX_EGRESS_ENABLED": "false",
}


def test_missing_backend_fails_at_startup():
    with pytest.raises(ConfigError, match="SANDBOX_BACKEND"):
        create_app_from_env({"SANDBOX_API_TOKEN": TOKEN, "SANDBOX_EGRESS_ENABLED": "false"})


def test_missing_token_fails_at_startup():
    with pytest.raises(ConfigError, match="SANDBOX_API_TOKEN"):
        create_app_from_env({"SANDBOX_BACKEND": "fake", "SANDBOX_EGRESS_ENABLED": "false"})


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


def test_default_egress_uses_the_policy_shipped_with_the_service():
    # No SANDBOX_EGRESS_POLICY_PATH: the file next to the service is picked up, so a
    # deployment that carries it needs no extra configuration.
    app = create_app_from_env({**FAKE_ENV, "SANDBOX_EGRESS_ENABLED": "true"})

    assert app is not None


def test_default_egress_with_a_missing_policy_file_fails_at_startup(tmp_path):
    with pytest.raises(ConfigError, match="SANDBOX_EGRESS_POLICY_PATH"):
        create_app_from_env(
            {
                **FAKE_ENV,
                "SANDBOX_EGRESS_ENABLED": "true",
                "SANDBOX_EGRESS_POLICY_PATH": str(tmp_path / "missing.json"),
            }
        )


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

    monkeypatch.setattr(bootstrap, "build_backend", lambda env, clock: Closable())
    app = create_app_from_env(FAKE_ENV)
    async with app.router.lifespan_context(app):
        assert closed == []
    assert closed == [True]
