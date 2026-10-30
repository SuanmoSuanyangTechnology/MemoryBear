"""The container: runtime, shared services and one facade per registered domain.

``src.composition.registry`` is the single list of task domains; this module only
builds the cross-domain services once and turns each registered module into an
attribute. Tasks ask for a domain facade (``container.profile_cache``) and never
see an adapter. Adding a task domain changes the registry, not this file.

The facades are plain instance attributes, not a catch-all ``__getattr__``: a
misspelled attribute must fail loudly instead of resolving to ``Any``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from src.composition.registry import MODULES
from src.composition.shared import build_shared_dependencies
from src.runtime import JobsRuntime, get_runtime

if TYPE_CHECKING:
    from src.composition.interest import InterestWiring
    from src.composition.profile_cache import ProfileCacheWiring


class Container:
    """Process-wide wiring root; cheap to build, one instance per process."""

    if TYPE_CHECKING:
        # Supplied by the registry at runtime; annotated so callers stay typed.
        profile_cache: ProfileCacheWiring
        interest: InterestWiring

    def __init__(self, runtime: JobsRuntime) -> None:
        shared = build_shared_dependencies(runtime)
        self._facades: dict[str, Any] = {
            module.name: module.build(runtime, shared) for module in MODULES
        }
        for name, facade in self._facades.items():
            setattr(self, name, facade)


_container: Container | None = None
_container_lock = threading.Lock()


def get_container() -> Container:
    """Return the process-wide container, creating it on first use."""

    global _container
    if _container is None:
        with _container_lock:
            if _container is None:
                _container = Container(get_runtime())
    return _container


def set_container(container: Container | None) -> None:
    """Replace the process container (tests and startup wiring)."""

    global _container
    with _container_lock:
        _container = container
