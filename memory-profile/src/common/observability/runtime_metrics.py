"""Low-overhead process, GC, and allocator snapshots for the health probe."""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import platform
import sys
import threading
import time
import tracemalloc
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psutil

try:
    import resource as _resource
except ImportError:  # Windows has no resource module
    _resource = None

if TYPE_CHECKING:
    from src.config import ProfileSettings

logger = logging.getLogger(__name__)

try:
    _PROCESS: psutil.Process | None = psutil.Process(os.getpid())
    # Seed the CPU percentage baseline; the first real call would otherwise
    # always report 0.0 because psutil measures the delta since the last call.
    _PROCESS.cpu_percent(interval=None)
except Exception:  # pragma: no cover - defensive for restricted runtimes
    _PROCESS = None

# psutil.Process.num_threads() enumerates every OS thread on Windows (~6 ms per
# call); a short cache keeps the diagnostic endpoint from blocking the loop.
_thread_count_cache: tuple[float, int] | None = None


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def current_rss_bytes() -> int | None:
    """Current resident set size, or ``None`` when the platform hides it."""

    if _PROCESS is None:
        return None
    try:
        return int(_PROCESS.memory_info().rss)
    except Exception:
        return None


def _peak_rss_bytes(memory_info: Any) -> int | None:
    if _resource is not None:
        try:
            value = int(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss)
            # Linux reports KiB; macOS reports bytes.
            return value if sys.platform == "darwin" else value * 1024
        except Exception:
            return None
    try:
        peak = getattr(memory_info, "peak_wset", None)
        return int(peak) if peak is not None else None
    except Exception:
        return None


def _process_thread_count() -> int | None:
    global _thread_count_cache
    if _PROCESS is None:
        return None
    now = time.monotonic()
    cached = _thread_count_cache
    if cached is not None and now - cached[0] < 10.0:
        return cached[1]
    try:
        count = int(_PROCESS.num_threads())
    except Exception:
        return None
    _thread_count_cache = (now, count)
    return count


def _open_fds() -> int | None:
    if _PROCESS is None:
        return None
    try:
        return int(_PROCESS.num_fds())
    except Exception:
        return None


def _open_handles() -> int | None:
    if _PROCESS is None:
        return None
    try:
        return int(_PROCESS.num_handles())
    except Exception:
        return None


def collect_process_metrics(baseline_rss: int | None) -> dict[str, Any]:
    """Read process memory/CPU counters without touching any dependency."""

    if _PROCESS is None:
        return {
            "rss_bytes": None,
            "vms_bytes": None,
            "peak_rss_bytes": None,
            "baseline_rss_bytes": baseline_rss,
            "rss_growth_bytes": None,
            "cpu_time_seconds": None,
            "cpu_percent": None,
            "threads": threading.active_count(),
            "open_fds": None,
            "open_handles": None,
        }

    try:
        memory_info = _PROCESS.memory_info()
        rss = int(memory_info.rss)
        vms = int(memory_info.vms)
    except Exception:
        memory_info = None
        rss = None
        vms = None

    try:
        cpu_times = _PROCESS.cpu_times()
        cpu_time_seconds = round(float(cpu_times.user + cpu_times.system), 3)
    except Exception:
        cpu_time_seconds = None

    try:
        cpu_percent = round(float(_PROCESS.cpu_percent(interval=None)), 2)
    except Exception:
        cpu_percent = None

    process_threads = _process_thread_count()

    return {
        "rss_bytes": rss,
        "vms_bytes": vms,
        "peak_rss_bytes": _peak_rss_bytes(memory_info),
        "baseline_rss_bytes": baseline_rss,
        "rss_growth_bytes": (
            rss - baseline_rss if rss is not None and baseline_rss is not None else None
        ),
        "cpu_time_seconds": cpu_time_seconds,
        "cpu_percent": cpu_percent,
        "threads": process_threads if process_threads is not None else threading.active_count(),
        "open_fds": _open_fds(),
        "open_handles": _open_handles(),
    }


def collect_python_metrics() -> dict[str, Any]:
    """GC counters, Python thread count, and event-loop task backlog."""

    gc_stats = gc.get_stats()
    try:
        # all_tasks() only returns tasks that are still pending.
        task_count = len(asyncio.all_tasks())
    except RuntimeError:
        # No running event loop (for example, a synchronous test call).
        task_count = None

    return {
        "version": platform.python_version(),
        "gc": {
            "counts": [int(value) for value in gc.get_count()],
            "collections": [int(item.get("collections", 0)) for item in gc_stats],
            "collected": [int(item.get("collected", 0)) for item in gc_stats],
            "uncollectable": [int(item.get("uncollectable", 0)) for item in gc_stats],
            "garbage": len(gc.garbage),
        },
        "threads": threading.active_count(),
        "tasks": task_count,
    }


def _format_frames(traceback: Any, frames: int) -> list[str]:
    """Render allocation frames relative to the working directory."""

    rendered: list[str] = []
    for frame in list(traceback)[: max(1, frames)]:
        filename = str(frame.filename)
        try:
            filename = os.path.relpath(filename)
        except ValueError:
            filename = os.path.basename(filename)
        rendered.append(f"{filename}:{frame.lineno}")
    return rendered


def collect_tracemalloc_metrics(top: int, frames: int) -> dict[str, Any]:
    """Report Python heap totals and, on request, the largest allocation sites."""

    if not tracemalloc.is_tracing():
        return {
            "enabled": False,
            "current_bytes": 0,
            "peak_bytes": 0,
            "top": [],
        }

    current, peak = tracemalloc.get_traced_memory()
    payload: dict[str, Any] = {
        "enabled": True,
        "current_bytes": int(current),
        "peak_bytes": int(peak),
        "top": [],
    }
    if top <= 0:
        return payload

    try:
        snapshot = tracemalloc.take_snapshot()
        statistics = snapshot.statistics("traceback")[:top]
        payload["top"] = [
            {
                "size_bytes": int(stat.size),
                "count": int(stat.count),
                "traceback": _format_frames(stat.traceback, frames),
            }
            for stat in statistics
        ]
    except Exception as exc:
        logger.warning(
            "Failed to take tracemalloc snapshot: %s",
            type(exc).__name__,
            extra={"event": "tracemalloc_snapshot_failed"},
        )
    return payload


def start_tracemalloc(settings: ProfileSettings) -> None:
    """Enable tracemalloc at startup when configured; never re-start mid-flight."""

    if not settings.memory_tracemalloc_enabled or tracemalloc.is_tracing():
        return
    tracemalloc.start(max(1, settings.memory_tracemalloc_frames))
    logger.warning(
        "tracemalloc enabled frames=%s; allocation tracing adds measurable overhead",
        settings.memory_tracemalloc_frames,
        extra={"event": "tracemalloc_enabled"},
    )
