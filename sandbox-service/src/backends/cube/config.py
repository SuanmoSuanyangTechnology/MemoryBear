"""Configuration of the Cube backend: ``SANDBOX_CUBE_*`` environment variables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...envconf import ConfigError, build, variable_name

PREFIX = "SANDBOX_CUBE_"
REQUIRED = ("api_url", "proxy_addr")


@dataclass(frozen=True)
class CubeConfig:
    # Control plane (CubeAPI), e.g. ``http://10.0.0.5:3000``.
    api_url: str
    # Data plane entry (CubeProxy) as ``host:port``; sandboxes are reached through it.
    proxy_addr: str
    # Bearer credential for CubeAPI once the auth callback is enabled. Never logged.
    api_key: str | None = field(default=None, repr=False)
    proxy_scheme: str = "http"
    # ``host``: send ``Host: <port>-<sandbox>.<domain>`` to ``proxy_addr`` (no DNS needed).
    # ``path``: use ``/sandbox/<sandbox>/<port>/...`` on ``proxy_addr``.
    route_mode: str = "host"
    domain: str = "cube.app"
    code_port: int = 49999
    # envd (files / processes) inside the sandbox. Its Host header must NOT carry the
    # domain: with ``<port>-<sandbox>.<domain>`` CubeProxy answers GET /files with a 302
    # to a web-block page (verified on a real node), so ``envd_domain`` defaults to none.
    envd_port: int = 49983
    envd_domain: str | None = None
    envd_user: str = "root"
    file_timeout_s: float = 120.0  # per read/write of a file stream, not the whole transfer
    http_timeout_s: float = 30.0
    create_timeout_s: float = 120.0
    connect_timeout_s: float = 5.0
    # Page size of ``GET /v2/sandboxes``; the endpoint has no cursor, so this is a hard cap.
    list_limit: int = 1000

    def __post_init__(self) -> None:
        if not self.api_url.startswith(("http://", "https://")):
            raise ValueError("api_url must start with http:// or https://")
        if self.proxy_scheme not in ("http", "https"):
            raise ValueError("proxy_scheme must be http or https")
        if self.route_mode not in ("host", "path"):
            raise ValueError("route_mode must be host or path")
        if "/" in self.proxy_addr:
            raise ValueError("proxy_addr must be host:port without a scheme or path")
        if not 0 < self.code_port < 65536:
            raise ValueError("code_port out of range")
        if not 0 < self.envd_port < 65536:
            raise ValueError("envd_port out of range")
        for name in ("http_timeout_s", "create_timeout_s", "connect_timeout_s", "file_timeout_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.list_limit < 1:
            raise ValueError("list_limit must be >= 1")


def load_config(env: Mapping[str, str]) -> CubeConfig:
    missing = [
        variable_name(PREFIX, name)
        for name in REQUIRED
        if not (env.get(variable_name(PREFIX, name)) or "").strip()
    ]
    if missing:
        raise ConfigError(f"{'、'.join(missing)} 未设置")
    return build(CubeConfig, env, PREFIX)
