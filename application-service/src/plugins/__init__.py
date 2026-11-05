"""Plugin registry seam mirroring the monolith's ``app.plugins`` lookup.

Enterprise policy hooks (quota alert reporters, quota counters, the community
market's HTTP gateway and orchestration services) register here through the
private enterprise-extensions package. When nothing is registered every lookup
returns None and callers degrade to a no-op -- or, for features that cannot be
half-wired, fail closed with ``FEATURE_NOT_AVAILABLE`` (6020).

Two registries:

* ``_plugins`` -- named singletons / factories (``register_plugin``).
* ``_routers`` -- FastAPI routers for enterprise-only surfaces
  (``register_router``). The open-source build registers nothing, so those
  routes simply do not exist in the community edition.

Nothing here imports ``enterprise_ext``; the dependency points the other way,
from the private package into this seam.
"""

from __future__ import annotations

from typing import Any

_plugins: dict[str, Any] = {}
_routers: list[dict[str, Any]] = []


def register_plugin(name: str, instance: Any) -> None:
    _plugins[name] = instance


def get_plugin(name: str) -> Any | None:
    return _plugins.get(name)


def list_plugins() -> list[str]:
    return list(_plugins)


def register_router(router: Any, prefix: str = "", tags: list[str] | None = None) -> None:
    """Queue an enterprise router for mounting onto the internal API.

    The host mounts the queue at assembly time (``api.router.mount_registered_routers``),
    before ``internal_v1_router`` is attached to the FastAPI app. Routers keep
    their own path prefixes, so ``prefix`` is normally empty.
    """

    _routers.append({"router": router, "prefix": prefix, "tags": tags or []})


def get_registered_routers() -> list[dict[str, Any]]:
    return list(_routers)


def clear() -> None:
    """Drop every registration (test helper; production never calls this)."""

    _plugins.clear()
    _routers.clear()


__all__ = [
    "clear",
    "get_plugin",
    "get_registered_routers",
    "list_plugins",
    "register_plugin",
    "register_router",
]
