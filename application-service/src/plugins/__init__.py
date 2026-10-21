"""Plugin registry seam mirroring the monolith's ``app.plugins`` lookup.

Enterprise policy hooks (quota alert reporters, quota counters) register here
through the private enterprise-extensions package. When nothing is registered
every lookup returns None and callers degrade to a no-op.
"""

from __future__ import annotations

from typing import Any

_plugins: dict[str, Any] = {}


def register_plugin(name: str, instance: Any) -> None:
    _plugins[name] = instance


def get_plugin(name: str) -> Any | None:
    return _plugins.get(name)


def list_plugins() -> list[str]:
    return list(_plugins)


__all__ = ["get_plugin", "list_plugins", "register_plugin"]
