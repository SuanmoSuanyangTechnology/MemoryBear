"""The wire shape of every job result.

Application handlers return their own result objects; this module is the only
place that turns one into the JSON a caller reads. Keeping it here means a use
case does not decide what a caller sees, and another executor adapter reuses the
same shapes.

The key names below are a cross-service contract, not an implementation detail:

* ``memory-profile`` reads ``items``/``cacheable`` from the interest result,
  ``status``/``end_user_id``/``insight_success``/``summary_success``/``errors``
  from a profile-cache unit, and ``total_users``/``successful``/``failed``/
  ``errors`` from the workspace fan-out;
* the scan and pre-warm payloads are operational reports nobody parses, but
  ``status`` and the counters have always been there.

The executor adapter appends the attempt's ``task_id`` after serialization, so
it is not part of any shape below.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.application.dto import FanOutReport, ScanReport
from src.application.interest.generate import InterestDistributionResult
from src.application.jobs.health_ping import HealthReport
from src.application.jobs.interest_prewarm import PrewarmJobResult
from src.application.profile_cache.generate import ProfileCacheResult


def _health(report: HealthReport) -> dict[str, Any]:
    return {"dependencies": report.dependencies}


def _scan(report: ScanReport) -> dict[str, Any]:
    return {
        "status": "SUCCESS",
        "dispatched": report.dispatched,
        "skip_no_change": report.skip_no_change,
        "skip_fresh": report.skip_fresh,
        "skip_inflight": report.skip_inflight,
        "failed": report.failed,
        "page_failures": report.page_failures,
        "elapsed_seconds": report.elapsed_seconds,
    }


def _fan_out(report: FanOutReport) -> dict[str, Any]:
    return {
        "total_users": report.total_users,
        "successful": report.successful,
        "failed": report.failed,
        "errors": report.errors,
    }


def _profile_cache(result: ProfileCacheResult) -> dict[str, Any]:
    if result.skipped_reason is not None:
        return {
            "status": "skipped",
            "reason": result.skipped_reason,
            "end_user_id": result.end_user_id,
            "workspace_id": result.workspace_id,
        }
    return {
        "end_user_id": result.end_user_id,
        "insight_success": result.insight_success,
        "summary_success": result.summary_success,
        "errors": result.errors,
    }


def _interest(result: InterestDistributionResult) -> dict[str, Any]:
    return {"items": result.items, "cacheable": result.cacheable}


def _prewarm(result: PrewarmJobResult) -> dict[str, Any]:
    report = result.report
    return {
        "status": "SUCCESS",
        "dispatched": report.dispatched,
        "skipped_cached": report.skipped_cached,
        "skipped_missing": report.skipped_missing,
        "invalid": result.invalid,
        "failed": report.failed,
        "errors": report.errors,
        "elapsed_seconds": result.elapsed_seconds,
    }


_SERIALIZERS: dict[type, Callable[[Any], dict[str, Any]]] = {
    HealthReport: _health,
    ScanReport: _scan,
    FanOutReport: _fan_out,
    ProfileCacheResult: _profile_cache,
    InterestDistributionResult: _interest,
    PrewarmJobResult: _prewarm,
}


def serialize(result: object) -> dict[str, Any]:
    """Render one job result; an unknown type is a wiring bug, not a payload."""

    serializer = _SERIALIZERS.get(type(result))
    if serializer is None:
        raise TypeError(
            f"no serializer for job result {type(result).__name__}; "
            "add it to src/interfaces/tasks/serialization.py"
        )
    return serializer(result)
