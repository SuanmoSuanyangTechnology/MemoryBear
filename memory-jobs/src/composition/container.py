"""The container: runtime, shared dependencies and one facade per task domain.

Tasks ask the container for a domain facade (``container.profile_cache``) and
never see an adapter. Adding a task domain means adding a wiring module and one
property here; adding a use case to an existing domain changes only its wiring.
"""

from __future__ import annotations

import threading

from src.composition.interest import InterestWiring
from src.composition.profile_cache import ProfileCacheWiring
from src.composition.shared import build_shared_dependencies
from src.runtime import JobsRuntime, get_runtime


class Container:
    """Process-wide wiring root; cheap to build, one instance per process."""

    def __init__(self, runtime: JobsRuntime) -> None:
        self.runtime = runtime
        self._dependencies = build_shared_dependencies(runtime)

    @property
    def profile_cache(self) -> ProfileCacheWiring:
        return ProfileCacheWiring(self._dependencies)

    @property
    def interest(self) -> InterestWiring:
        return InterestWiring(self._dependencies)


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
