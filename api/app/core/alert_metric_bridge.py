"""Core 到企业告警插件的尽力而为桥接层。

两条出口：
- 内联发射（本进程调用点直接上报）：`report_*` 系列，覆盖宿主本地模型调用/认证路径；
- 用量事件流消费（B9）：`consume_model_gateway_alerts` 读 `model:usage` 流，
  把服务侧（model-service）的调用终态喂给同一套网关健康规则。

双报防护约定：`source_service == SOURCE_SERVICE`（宿主本地发射）的事件由内联路径
负责告警，流消费一律跳过；若新增宿主本地发射点，必须同时接内联上报，否则该来源
的失败不会被评估。
"""
from __future__ import annotations

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

from app.aioRedis import get_thread_safe_sync_redis
from app.core.logging_config import get_logger
from app.core.usage_bridge import SOURCE_SERVICE
from app.plugins import get_plugin
from app.usage_publisher import MODEL_USAGE_STREAM

logger = get_logger(__name__)

#: 成功调用恢复探测节流（秒）：模型调用是热路径，同一模型配置在每个进程内
#: 最多探测一次恢复，避免每次成功调用都触发完整告警评估。
_GATEWAY_SUCCESS_PROBE_INTERVAL_SECONDS = 60.0

#: 进程内节流表：键为配置身份，值为上次探测的单调时钟。
#: 多 worker 进程各自节流，最坏每进程多一次探测，成本有界。
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
    """只识别网络、认证和服务端错误，排除参数/内容等业务错误。"""
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
    """在 API Key 有效性校验成功后上报剩余有效期。"""
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
    """异步认证路径仅把基础值传入线程，避免跨线程使用 ORM 实例。"""
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
    """真实模型调用最终失败后上报；绝不传播告警侧异常。"""
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
    """异步模型路径在线程中执行同步告警评估。"""
    if not is_model_gateway_failure(exc):
        return
    await asyncio.to_thread(
        report_model_gateway_failure, config, operation, exc, started_at
    )


def _gateway_success_probe_allowed(config: Any) -> bool:
    """进程内节流：同一模型配置每 60 秒最多允许一次成功恢复探测。"""
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
        # 顺带清理长期未探测的条目，防止节流表随历史配置无限增长。
        if len(_gateway_success_probe_at) > 4096:
            stale = [k for k, v in _gateway_success_probe_at.items() if now - v > 300]
            for key in stale:
                _gateway_success_probe_at.pop(key, None)
    return True


def _do_report_model_gateway_success(
    config: Any, operation: str, started_at: float
) -> None:
    """恢复探测的实际执行；绝不传播告警侧异常。"""
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
    """真实模型调用成功后上报 healthy=1，驱动网关告警自动恢复。

    失败告警只有失败观测，事件会永远停留在 firing；成功观测让评估器
    判定条件未命中并自动 resolve。经进程内节流，同一配置每分钟最多探测一次。
    """
    if not _gateway_success_probe_allowed(config):
        return
    _do_report_model_gateway_success(config, operation, started_at)


async def report_model_gateway_success_async(
    config: Any, operation: str, started_at: float
) -> None:
    """异步模型路径：通过节流后才在线程中执行恢复探测。"""
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
    """尽力而为投递登录失败；原始主体只在 Reporter 内用于 HMAC。"""
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
    """异步认证入口在线程中投递，告警侧故障不影响登录响应。"""
    await asyncio.to_thread(report_login_failure, **kwargs)


# ---------------- 用量事件流消费：网关健康告警（B9） ----------------

#: 独立于落表消费（服务侧 `model-usage-consumers`）的消费组：同组 `>` 语义下每条消息
#: 只投给组内一个消费者，共用组会让落表与告警两边都读不全。
MODEL_USAGE_ALERT_CONSUMER_GROUP = "model-usage-alerts"

_ALERT_BATCH_SIZE = 200
_ALERT_MAX_BATCHES_PER_RUN = 5
#: 观测新鲜度窗口（秒）：worker 停机后积压的旧事件不再评估，避免把早已恢复的失败重新点亮
_ALERT_MAX_AGE_SECONDS = 300.0
_ALERT_RECLAIM_MIN_IDLE_MS = 60_000

#: 服务侧换渠道编排聚合出的网关失败类型（瞬时/可换渠道错误耗尽 → 按构造必属网关类）
_STREAM_GATEWAY_ERROR_TYPES = ("channelswitchexhaustederror",)


def _consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def is_model_gateway_failure_type(error_type: str | None) -> bool:
    """按事件 `error_type`（异常类名）判定网关类失败。

    stream 事件不带 HTTP 状态码，内联 `is_model_gateway_failure` 的状态分支不可复刻：
    服务侧超时/连接/401/403/429/5xx/凭据解密失败已被编排聚合为
    ``ChannelSwitchExhaustedError``，其余（terminal 4xx 等）原样透传，故以聚合类型名
    + 名字 token 匹配覆盖。
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
            MODEL_USAGE_STREAM, MODEL_USAGE_ALERT_CONSUMER_GROUP, id="$", mkstream=True
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _parse_alert_event(payload: str) -> UsageEvent:
    """事件 JSON → 契约模型；非法/缺字段抛异常（调用方按坏消息丢弃）。"""
    return UsageEvent.model_validate(json.loads(payload))


def _is_stale(event: UsageEvent) -> bool:
    age_ms = int(time.time() * 1000) - event.ts_ms
    return age_ms > int(_ALERT_MAX_AGE_SECONDS * 1000)


def _evaluate_observations(
    reporter: Any,
    observations: dict[UUID, tuple[UsageEvent, bool]],
    stats: dict[str, int],
) -> int:
    """每个 config 评估一次；插件缺失/评估异常仅记日志——告警是旁路观测，不重试。"""
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
    """过滤 → 同批按 config 去重（取最后一条观测）→ 评估；返回可 ACK 的消息 id（无条件 ACK）。"""
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
            # 宿主本地发射：调用点已内联上报，跳过防双报
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
            failed = False  # ok / fallback_succeeded → 恢复观测
        observations[event.config_id] = (event, failed)
    stats["evaluated"] += _evaluate_observations(reporter, observations, stats)
    return [message_id for message_id, _ in entries]


def _reclaim_stale_entries(client: Any, reporter: Any, stats: dict[str, int]) -> None:
    """XAUTOCLAIM 兜底：领取 idle 超阈的 pending（评估中崩溃的进程遗留）。"""
    cursor = "0-0"
    while True:
        try:
            claimed = client.xautoclaim(
                MODEL_USAGE_STREAM,
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
                client.xack(MODEL_USAGE_STREAM, MODEL_USAGE_ALERT_CONSUMER_GROUP, *ack_ids)
        if not messages or cursor in ("0-0", "0", None):
            return


def consume_model_gateway_alerts() -> dict[str, Any]:
    """消费 `model:usage` 事件流，按网关健康规则评估（尽力而为 + 新鲜度优先）。

    仅供 beat 周期任务调用（同步 Redis + 同步告警引擎）；社区版无 premium 插件时
    直接跳过——不建组、不读流，零足迹。

    投递语义与落表消费（至少一次）不同：每条处理完**无条件 ACK**。告警是旁路观测，
    重放陈旧失败会把已恢复的调用重新点亮；评估异常仅记日志，不重试。
    Redis 层异常上抛，由任务壳兜（下轮重试）。
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
            {MODEL_USAGE_STREAM: ">"},
            count=_ALERT_BATCH_SIZE,
        )
        entries = [entry for _stream, messages in (response or []) for entry in messages]
        if not entries:
            break
        ack_ids = _handle_alert_entries(entries, reporter, stats)
        if ack_ids:
            client.xack(MODEL_USAGE_STREAM, MODEL_USAGE_ALERT_CONSUMER_GROUP, *ack_ids)
    return {"status": "SUCCESS", **stats}
