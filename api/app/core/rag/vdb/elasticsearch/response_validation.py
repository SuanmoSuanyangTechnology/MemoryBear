from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def failure_summary(failures: Any) -> str:
    if not isinstance(failures, list) or not failures:
        return ""
    summaries: list[str] = []
    for failure in failures[:3]:
        if isinstance(failure, Mapping):
            reason = failure.get("reason") or failure.get("type") or failure.get("cause")
            if isinstance(reason, Mapping):
                reason = reason.get("reason") or reason.get("type")
            summaries.append(str(reason or "unknown"))
        else:
            summaries.append(str(failure))
    return "; ".join(summaries)


def raise_on_search_response_failure(
    response: Mapping[str, Any],
    context: str,
) -> None:
    if response.get("timed_out"):
        raise RuntimeError(f"Elasticsearch search timed out during {context}")

    shards = response.get("_shards") or {}
    failed = int_value(shards.get("failed") if isinstance(shards, Mapping) else 0)
    failures: list[Any] = []
    if isinstance(shards, Mapping):
        shard_failures = shards.get("failures") or []
        if isinstance(shard_failures, list):
            failures.extend(shard_failures)

    response_failures = response.get("failures") or []
    if isinstance(response_failures, list):
        failures.extend(response_failures)

    if failed or failures:
        details = failure_summary(failures)
        message = (
            f"Elasticsearch search failed during {context}: "
            f"failed_shards={failed} failures={len(failures)}"
        )
        if details:
            message = f"{message}: {details}"
        raise RuntimeError(message)


def raise_on_delete_by_query_failure(
    response: Mapping[str, Any],
    context: str,
) -> None:
    if response.get("timed_out"):
        raise RuntimeError(f"Elasticsearch delete_by_query timed out during {context}")

    version_conflicts = int_value(response.get("version_conflicts"))
    failures = response.get("failures") or []
    if version_conflicts or failures:
        details = failure_summary(failures)
        message = (
            f"Elasticsearch delete_by_query failed during {context}: "
            f"version_conflicts={version_conflicts} failures={len(failures)}"
        )
        if details:
            message = f"{message}: {details}"
        raise RuntimeError(message)

    if "deleted" not in response:
        raise RuntimeError(
            f"Elasticsearch delete_by_query returned no deleted count during {context}"
        )
