"""Process entry point.

Run with ``uvicorn --factory src.main:create_app_from_env``. Configuration errors
(missing backend, missing token, bad numbers) surface here, at start-up.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from .api.app import create_app
from .api.auth import load_api_token
from .backends.factory import build_backend
from .clock import Clock
from .config import load_config
from .egress.policy import load_strict_egress_policy
from .envconf import ConfigError
from .observability import JsonlOperationAuditSink
from .services.manager import SandboxManager
from .services.runner import BackgroundRunner

# Auto-load the service's .env (sits next to pyproject.toml, one level above src/).
# Existing real environment variables always win; if no .env exists (production injects
# env directly) this is a no-op.
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_FILE, override=False)


def create_app_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    source = os.environ if env is None else env
    cfg = load_config(source)
    token = load_api_token(source)
    egress_policy = load_strict_egress_policy(cfg.egress, base_dir=_ENV_FILE.parent)
    clock = Clock()
    backend = build_backend(source, clock)

    # The substrate's own expiry timer must not fire before the scheduler decides.
    native_ttl_s = getattr(backend, "native_ttl_s", None)
    if native_ttl_s is not None and native_ttl_s < cfg.scheduler.absolute_lifetime_s:
        raise ConfigError(
            f"backend native_ttl_s={native_ttl_s} is below "
            f"SANDBOX_ABSOLUTE_LIFETIME_S={cfg.scheduler.absolute_lifetime_s}"
        )

    audit_sink = JsonlOperationAuditSink(cfg.audit.audit_log_path)
    manager = SandboxManager(
        backend,
        cfg.scheduler,
        clock=clock,
        budget=cfg.budget,
        files=cfg.files,
        audit_sink=audit_sink,
        egress_policy=egress_policy,
    )
    runner = BackgroundRunner(manager, cfg.scheduler, clock=clock)

    async def close_resources() -> None:
        try:
            aclose = getattr(backend, "aclose", None)
            if aclose is not None:
                await aclose()
        finally:
            audit_sink.close()

    return create_app(manager, api_token=token, runner=runner, on_close=close_resources)
