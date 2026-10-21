"""Best-effort bridge from core to the enterprise alert plugins.

Two outlets:
- Inline emission (direct reporting from call sites in this process): the `report_*`
  family, covering host-local model-call/auth paths;
- Usage-event stream consumption: `consume_model_gateway_alerts` reads the deployment-scoped
  usage stream (`model:usage:<DB_NAME>`) and feeds service-side (model-service) call terminal
  states into the same gateway health rules.

Double-report protection: events with `source_service == SOURCE_SERVICE` (host-local
emission) are alerted by the inline path and always skipped by stream consumption; if a
new host-local emission point is added, it must also wire inline reporting, otherwise
failures from that source will not be evaluated.
"""
from __future__ import annotations

import logging

import asyncio
import hashlib
import json
import os
import socket
import threading
import time
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError
from redbear_model import UsageEvent, UsageStatus
from redis.exceptions import ResponseError

from .aio_redis import get_thread_safe_sync_redis
from .usage_bridge import SOURCE_SERVICE
from .plugins import get_plugin
from .usage_publisher import model_usage_stream

logger = logging.getLogger(__name__)

#: Success-call recovery probe throttle (seconds): model calls are a hot path, so
#: within each process the same model config probes recovery at most once, avoiding a
#: full alert evaluation on every successful call.
_GATEWAY_SUCCESS_PROBE_INTERVAL_SECONDS = 60.0

#: In-process throttle table: key is the config identity, value is the monotonic clock
#: of the last probe. Worker processes throttle independently; at worst each process
#: probes one extra time, bounding the cost.
_gateway_success_probe_at: dict[str, float] = {}
_gateway_success_probe_lock = threading.Lock()
_GATEWAY_ERROR_NAMES = (
    "authentication",
    "apiconnection",
    "connectionerror",
    "connecterror",
    "credentialretrieval",
    "nocredentials",
    "serviceunavailable",
    "timeout",
    "unrecognizedclient",
)


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if value is None and isinstance(response, dict):
        value = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if value is None:
        value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def is_model_gateway_failure(exc: BaseException) -> bool:
    """Recognize only network, auth and server-side errors; exclude business errors such as bad parameters/content."""
    current: BaseException | None = exc
    for _ in range(4):
        if current is None:
            break
        if isinstance(current, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError)):
            return True
        status = _status_code(current)
        if status in {401, 403} or (status is not None and status >= 500):
            return True
        name = type(current).__name__.lower()
        if any(token in name for token in _GATEWAY_ERROR_NAMES):
            return True
        current = current.__cause__ or current.__context__
    return False


def _report_api_key_expiry_values(
    *,
    api_key_id: Any,
    workspace_id: Any,
    api_key_name: str,
    api_key_type: str,
    expires_at: Any,
) -> None:
    try:
        reporter = get_plugin("api_key_expiry_alert_reporter")
        if reporter is not None:
            reporter.evaluate(
                api_key_id=api_key_id,
                workspace_id=workspace_id,
                api_key_name=api_key_name,
                api_key_type=api_key_type,
                expires_at=expires_at,
            )
    except Exception as exc:
        logger.error(
            "API Key 有效期告警上报失败: api_key_id=%s error=%s",
            api_key_id, type(exc).__name__, exc_info=True,
        )


def report_api_key_expiry(api_key: Any) -> None:
    """Report the remaining validity after API-key validation succeeds."""
    expires_at = getattr(api_key, "expires_at", None)
    if expires_at is None:
        return
    _report_api_key_expiry_values(
        api_key_id=api_key.id,
        workspace_id=api_key.workspace_id,
        api_key_name=api_key.name,
        api_key_type=api_key.type,
        expires_at=expires_at,
    )


async def report_api_key_expiry_async(api_key: Any) -> None:
    """The async auth path passes only primitive values into the thread, avoiding cross-thread use of ORM instances."""
    expires_at = getattr(api_key, "expires_at", None)
    if expires_at is None:
        return
    values = {
        "api_key_id": api_key.id,
        "workspace_id": api_key.workspace_id,
        "api_key_name": api_key.name,
        "api_key_type": api_key.type,
        "expires_at": expires_at,
    }
    await asyncio.to_thread(_report_api_key_expiry_values, **values)


def report_model_gateway_failure(
    config: Any,
    operation: str,
    exc: BaseException,
    started_at: float,
) -> None:
    """Report after a real model call ultimately fails; never propagates alert-side exceptions."""
    if not is_model_gateway_failure(exc):
        return
    try:
        reporter = get_plugin("model_gateway_health_reporter")
        if reporter is not None:
            reporter.evaluate_failure(
                model_name=config.model_name,
                provider=config.provider,
                api_key=config.api_key,
                operation=operation,
                error_type=type(exc).__name__,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
    except Exception as report_exc:
        logger.error(
            "模型网关告警上报失败: provider=%s model=%s operation=%s error=%s",
            getattr(config, "provider", None),
            getattr(config, "model_name", None),
            operation,
            type(report_exc).__name__,
            exc_info=True,
        )


async def report_model_gateway_failure_async(
    config: Any,
    operation: str,
    exc: BaseException,
    started_at: float,
) -> None:
    """The async model path runs the synchronous alert evaluation in a thread."""
    if not is_model_gateway_failure(exc):
        return
    await asyncio.to_thread(
        report_model_gateway_failure, config, operation, exc, started_at
    )


def _gateway_success_probe_allowed(config: Any) -> bool:
    """In-process throttle: the same model config allows at most one success-recovery probe per 60 seconds."""
    api_key = str(getattr(config, "api_key", "") or "")
    identity = "{}:{}:{}".format(
        getattr(config, "provider", ""),
        getattr(config, "model_name", ""),
        hashlib.sha1(api_key.encode("utf-8")).hexdigest()[:16],
    )
    now = time.monotonic()
    with _gateway_success_probe_lock:
        last = _gateway_success_probe_at.get(identity)
        if last is not None and now - last < _GATEWAY_SUCCESS_PROBE_INTERVAL_SECONDS:
            return False
        _gateway_success_probe_at[identity] = now
        # Also prune entries not probed for a long time, keeping the throttle table from
        # growing unboundedly with historical configs.
        if len(_gateway_success_probe_at) > 4096:
            stale = [k for k, v in _gateway_success_probe_at.items() if now - v > 300]
            for key in stale:
                _gateway_success_probe_at.pop(key, None)
    return True


def _do_report_model_gateway_success(
    config: Any, operation: str, started_at: float
) -> None:
    """Actual execution of the recovery probe; never propagates alert-side exceptions."""
    try:
        reporter = get_plugin("model_gateway_health_reporter")
        if reporter is not None:
            reporter.evaluate_success(
                model_name=config.model_name,
                provider=config.provider,
                api_key=config.api_key,
                operation=operation,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
    except Exception as exc:
        logger.error(
            "模型网关恢复上报失败: provider=%s model=%s operation=%s error=%s",
            getattr(config, "provider", None),
            getattr(config, "model_name", None),
            operation,
            type(exc).__name__,
            exc_info=True,
        )


def report_model_gateway_success(
    config: Any, operation: str, started_at: float
) -> None:
    """Report healthy=1 after a real model call succeeds, driving automatic recovery of gateway alerts.

    A failure alert has only failure observations and would stay in firing forever; a
    success observation lets the evaluator see the condition unmet and auto-resolve.
    Throttled per process, the same config probes at most once per minute.
    """
    if not _gateway_success_probe_allowed(config):
        return
    _do_report_model_gateway_success(config, operation, started_at)


async def report_model_gateway_success_async(
    config: Any, operation: str, started_at: float
) -> None:
    """Async model path: run the recovery probe in a thread only after passing the throttle."""
    if not _gateway_success_probe_allowed(config):
        return
    await asyncio.to_thread(
        _do_report_model_gateway_success, config, operation, started_at
    )


def report_login_failure(
    *,
    principal: str,
    auth_surface: str,
    reason_class: str,
    tenant_id: Any = None,
    principal_kind: str = "account",
) -> None:
    """Best-effort delivery of login failures; the raw principal is used for HMAC only inside the Reporter."""
    try:
        reporter = get_plugin("login_anomaly_alert_reporter")
        if reporter is not None:
            reporter.report_failure(
                principal=principal,
                auth_surface=auth_surface,
                reason_class=reason_class,
                tenant_id=tenant_id,
                principal_kind=principal_kind,
            )
    except Exception as exc:
        logger.error(
            "登录异常告警上报失败: surface=%s reason=%s error=%s",
            auth_surface, reason_class, type(exc).__name__, exc_info=True,
        )


async def report_login_failure_async(**kwargs: Any) -> None:
    """Async auth entry points deliver from a thread; alert-side failures do not affect the login response."""
    await asyncio.to_thread(report_login_failure, **kwargs)


# ---------------- Usage-event stream consumption: gateway health alerts ----------------

#: Consumer group independent of the persistence consumer (service-side
#: `model-usage-consumers`): under `>` semantics each message goes to only one consumer
#: in the group, so sharing a group would leave both sides reading partial data.
MODEL_USAGE_ALERT_CONSUMER_GROUP = "model-usage-alerts"

_ALERT_BATCH_SIZE = 200
_ALERT_MAX_BATCHES_PER_RUN = 5
#: Observation freshness window (seconds): old events backlogged while a worker is down
#: are no longer evaluated, avoiding re-firing failures that recovered long ago
_ALERT_MAX_AGE_SECONDS = 300.0
_ALERT_RECLAIM_MIN_IDLE_MS = 60_000

#: Gateway failure type aggregated by service-side channel-switch orchestration
#: (transient/switchable-channel errors exhausted -> gateway class by construction)
_STREAM_GATEWAY_ERROR_TYPES = ("channelswitchexhaustederror",)


def _consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def is_model_gateway_failure_type(error_type: str | None) -> bool:
    """Decide gateway-class failure by the event `error_type` (exception class name).

    Stream events carry no HTTP status code, so the status branch of the inline
    `is_model_gateway_failure` cannot be replicated: service-side timeout/connection/
    401/403/429/5xx/credential-decryption failures have been aggregated by the
    orchestration into ``ChannelSwitchExhaustedError``, while the rest (terminal 4xx,
    etc.) pass through unchanged; hence coverage via the aggregated type name plus
    name-token matching.
    """
    if not error_type:
        return False
    name = error_type.lower()
    if name in _STREAM_GATEWAY_ERROR_TYPES:
        return True
    return any(token in name for token in _GATEWAY_ERROR_NAMES)


def _ensure_alert_group(client: Any) -> None:
    try:
        client.xgroup_create(
            model_usage_stream(), MODEL_USAGE_ALERT_CONSUMER_GROUP, id="$", mkstream=True
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _parse_alert_event(payload: str) -> UsageEvent:
    """Event JSON -> contract model; invalid/missing fields raise (the caller drops it as a bad message)."""
    return UsageEvent.model_validate(json.loads(payload))


def _is_stale(event: UsageEvent) -> bool:
    age_ms = int(time.time() * 1000) - event.ts_ms
    return age_ms > int(_ALERT_MAX_AGE_SECONDS * 1000)


def _evaluate_observations(
    reporter: Any,
    observations: dict[UUID, tuple[UsageEvent, bool]],
    stats: dict[str, int],
) -> int:
    """Evaluate once per config; missing plugin / evaluation exception only logs — alerts are side-channel observations, no retries."""
    evaluated = 0
    for event, failed in observations.values():
        method_name = "evaluate_stream_failure" if failed else "evaluate_stream_success"
        method = getattr(reporter, method_name, None)
        if method is None:
            stats["eval_skipped"] += 1
            logger.warning(
                "网关告警插件缺少 %s（premium 未升级？），跳过评估: config=%s",
                method_name, event.config_id,
            )
            continue
        kwargs: dict[str, Any] = {
            "config_id": event.config_id,
            "tenant_id": event.tenant_id,
            "provider": event.provider.value,
            "operation": event.capability.value,
            "latency_ms": float(event.latency_ms),
        }
        if failed:
            kwargs["error_type"] = event.error_type or "unknown"
        try:
            method(**kwargs)
            evaluated += 1
        except Exception as exc:
            stats["eval_failed"] += 1
            logger.error(
                "网关告警评估失败: config=%s error=%s", event.config_id, type(exc).__name__
            )
    return evaluated


def _handle_alert_entries(
    entries: list[tuple[str, dict[str, str]]],
    reporter: Any,
    stats: dict[str, int],
) -> list[str]:
    """Filter -> dedupe by config within the batch (keep the last observation) -> evaluate; return ACK-able message ids (unconditional ACK)."""
    observations: dict[UUID, tuple[UsageEvent, bool]] = {}
    for message_id, fields in entries:
        stats["read"] += 1
        payload = fields.get("payload")
        if not payload:
            stats["malformed"] += 1
            logger.warning("用量消息缺 payload，已丢弃: id=%s", message_id)
            continue
        try:
            event = _parse_alert_event(payload)
        except (ValueError, TypeError, ValidationError) as exc:
            stats["malformed"] += 1
            logger.warning("用量消息非法，已丢弃: id=%s err=%s", message_id, exc)
            continue
        if event.source_service == SOURCE_SERVICE:
            # Host-local emission: the call site already reported inline, skip to prevent double reporting
            stats["skipped"] += 1
            continue
        if _is_stale(event):
            stats["stale"] += 1
            continue
        if event.status is UsageStatus.FAILED:
            if not is_model_gateway_failure_type(event.error_type):
                stats["skipped"] += 1
                continue
            failed = True
        else:
            failed = False  # ok / fallback_succeeded -> recovery observation
        observations[event.config_id] = (event, failed)
    stats["evaluated"] += _evaluate_observations(reporter, observations, stats)
    return [message_id for message_id, _ in entries]


def _reclaim_stale_entries(client: Any, reporter: Any, stats: dict[str, int]) -> None:
    """XAUTOCLAIM fallback: claim pending entries idle beyond the threshold (left over by processes that crashed mid-evaluation)."""
    cursor = "0-0"
    while True:
        try:
            claimed = client.xautoclaim(
                model_usage_stream(),
                MODEL_USAGE_ALERT_CONSUMER_GROUP,
                _consumer_name(),
                min_idle_time=_ALERT_RECLAIM_MIN_IDLE_MS,
                start_id=cursor,
                count=_ALERT_BATCH_SIZE,
            )
        except ResponseError as exc:
            logger.warning("告警消费 XAUTOCLAIM 失败（忽略，下轮重试）: %s", exc)
            return
        cursor, messages = claimed[0], claimed[1]
        if messages:
            stats["reclaimed"] += len(messages)
            ack_ids = _handle_alert_entries(messages, reporter, stats)
            if ack_ids:
                client.xack(model_usage_stream(), MODEL_USAGE_ALERT_CONSUMER_GROUP, *ack_ids)
        if not messages or cursor in ("0-0", "0", None):
            return


def consume_model_gateway_alerts() -> dict[str, Any]:
    """Consume the deployment-scoped usage stream and evaluate by the gateway health rules (best-effort + freshness first).

    Called only by the beat periodic task (sync Redis + sync alert engine); the
    community edition with no premium plugin skips entirely — no group created, no
    stream read, zero footprint.

    Delivery semantics differ from the persistence consumer (at-least-once): every
    message is **unconditionally ACKed** once processed. Alerts are side-channel
    observations, and replaying stale failures would re-light calls that already
    recovered; evaluation exceptions only log, no retry. Redis-layer exceptions
    propagate and are caught by the task shell (retried next round).
    """
    reporter = get_plugin("model_gateway_health_reporter")
    if reporter is None:
        return {"status": "SKIPPED"}

    stats = {
        "read": 0,
        "malformed": 0,
        "skipped": 0,
        "stale": 0,
        "evaluated": 0,
        "eval_failed": 0,
        "eval_skipped": 0,
        "reclaimed": 0,
    }
    client = get_thread_safe_sync_redis()
    _ensure_alert_group(client)
    _reclaim_stale_entries(client, reporter, stats)
    for _ in range(_ALERT_MAX_BATCHES_PER_RUN):
        response = client.xreadgroup(
            MODEL_USAGE_ALERT_CONSUMER_GROUP,
            _consumer_name(),
            {model_usage_stream(): ">"},
            count=_ALERT_BATCH_SIZE,
        )
        entries = [entry for _stream, messages in (response or []) for entry in messages]
        if not entries:
            break
        ack_ids = _handle_alert_entries(entries, reporter, stats)
        if ack_ids:
            client.xack(model_usage_stream(), MODEL_USAGE_ALERT_CONSUMER_GROUP, *ack_ids)
    return {"status": "SUCCESS", **stats}
