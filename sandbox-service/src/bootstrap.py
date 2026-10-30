"""Composition root: turn environment variables into a configured FastAPI app.

Same shape as the other services in this repository (``src/bootstrap.py`` loads the
process configuration, ``src/main.py`` exposes the module-level ``app``), with one
difference kept on purpose: the factory takes the environment mapping as an argument,
so tests can build an app from a plain dict instead of mutating ``os.environ``.

Configuration errors (missing backend, missing token, bad numbers, unusable egress
policy) surface here, at call time, before the app starts serving.
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
from .observability import build_audit_sink
from .services.manager import SandboxManager
from .services.runner import BackgroundRunner

# Auto-load the service's .env (sits next to pyproject.toml, one level above src/).
# Existing real environment variables always win; if no .env exists (production injects
# env directly) this is a no-op.
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_FILE, override=False)

__all__ = ["create_app_from_env"]


def create_app_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    """Build the app from ``env`` (default: ``os.environ``, after loading ``.env``)."""

    source = os.environ if env is None else env
    cfg = load_config(source)
    token = load_api_token(source)
    egress_policy = load_strict_egress_policy(cfg.egress, base_dir=_ENV_FILE.parent)
    clock = Clock()
    backend = build_backend(source, clock)

    audit_sink = build_audit_sink(cfg.audit)
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
