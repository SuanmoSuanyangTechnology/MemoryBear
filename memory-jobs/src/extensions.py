"""Optional task-package extensions for edition-specific jobs.

The open build registers only the jobs, domains and beat entries declared in
this repository. A deployment that ships an extra package — the
``enterprise_ext`` convention — points ``JOBS_EXTENSION_MODULES`` at one or
more comma-separated module names. Each module must expose::

    def build_extension() -> Extension: ...

and its jobs, domain wirings, routes, periodic entries and result serializers
are merged into the same registries the open build uses. Without the setting
the open build is unchanged and no optional package is imported.

Fail-closed: a module named in the setting must import and return an
``Extension``. A typo aborts startup instead of silently dropping a
deployment's jobs. A missing *inner* dependency raises the original import
error instead of being reported as a missing extension package.

This module is deliberately a leaf: it imports settings only, never the
composition root or the task registry. Extension modules therefore may import
``src.composition`` themselves without re-entering this loader.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.application.ports.jobs import JobSpec
from src.bootstrap import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExtensionJob:
    """One inbound job contributed by an extension package."""

    name: str
    spec: JobSpec
    build: Callable[[], Any]


@dataclass(frozen=True, slots=True)
class ExtensionDomain:
    """One container domain contributed by an extension package."""

    name: str
    build: Callable[[Any, Any], Any]


@dataclass(frozen=True, slots=True)
class Extension:
    """Everything an extension package merges into the host registries."""

    jobs: tuple[ExtensionJob, ...] = ()
    domains: tuple[ExtensionDomain, ...] = ()
    #: ``task name -> queue``. Every contributed job must appear here; the
    #: registry validates the merged table at import.
    routes: Mapping[str, str] = field(default_factory=dict)
    #: Beat entries contributed by the extension (``PeriodicTask`` objects).
    periodic: tuple[Any, ...] = ()
    #: Result type -> wire serializer, merged into the task serializer table.
    serializers: Mapping[type, Callable[[Any], dict[str, Any]]] = field(
        default_factory=dict
    )


_EXTENSION_CACHE: dict[tuple[str, ...], tuple[Extension, ...]] = {}


def load_extensions(settings: Any | None = None) -> tuple[Extension, ...]:
    """Import and cache the configured extension packages.

    The result is keyed by module names, so repeated calls from the registry,
    the container and the scheduler share one build. An unset setting is the
    open build: an empty tuple, no import.
    """

    settings = settings or get_settings()
    names = tuple(getattr(settings, "jobs_extension_module_names", ()))
    if not names:
        return ()

    cached = _EXTENSION_CACHE.get(names)
    if cached is not None:
        return cached

    extensions = tuple(_load_extension(name) for name in names)
    _EXTENSION_CACHE[names] = extensions
    return extensions


def _load_extension(module_name: str) -> Extension:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        # Only the named module (or its parent package) counts as "not
        # installed"; a missing inner dependency must keep its own traceback.
        if missing and (module_name == missing or module_name.startswith(f"{missing}.")):
            raise RuntimeError(
                f"JOBS_EXTENSION_MODULES names {module_name!r}, but it cannot be "
                "imported. Install the enterprise extension package, or unset the "
                "variable for the open build."
            ) from exc
        raise

    builder = getattr(module, "build_extension", None)
    if not callable(builder):
        raise RuntimeError(
            f"{module_name} must expose build_extension() -> Extension"
        )
    extension = builder()
    if not isinstance(extension, Extension):
        raise RuntimeError(
            f"{module_name}.build_extension() returned "
            f"{type(extension).__name__}, expected Extension"
        )
    logger.info(
        "Job extension loaded module=%s jobs=%d domains=%d",
        module_name,
        len(extension.jobs),
        len(extension.domains),
    )
    return extension


def extension_jobs(extensions: tuple[Extension, ...]) -> tuple[ExtensionJob, ...]:
    return tuple(job for extension in extensions for job in extension.jobs)


def extension_domains(
    extensions: tuple[Extension, ...],
) -> tuple[ExtensionDomain, ...]:
    return tuple(domain for extension in extensions for domain in extension.domains)


def extension_routes(extensions: tuple[Extension, ...]) -> dict[str, str]:
    return {
        name: queue
        for extension in extensions
        for name, queue in extension.routes.items()
    }


def extension_periodic(extensions: tuple[Extension, ...]) -> tuple[Any, ...]:
    return tuple(entry for extension in extensions for entry in extension.periodic)


def extension_serializers(
    extensions: tuple[Extension, ...],
) -> dict[type, Callable[[Any], dict[str, Any]]]:
    return {
        result_type: serializer
        for extension in extensions
        for result_type, serializer in extension.serializers.items()
    }


def clear_extension_cache() -> None:
    """Drop the per-process cache (tests and deliberate reconfiguration)."""

    _EXTENSION_CACHE.clear()


__all__ = [
    "Extension",
    "ExtensionDomain",
    "ExtensionJob",
    "clear_extension_cache",
    "extension_domains",
    "extension_jobs",
    "extension_periodic",
    "extension_routes",
    "extension_serializers",
    "load_extensions",
]
