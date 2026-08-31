"""Core 到企业告警插件的尽力而为桥接层。"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from typing import Any

import httpx

from app.core.logging_config import get_logger
from app.plugins import get_plugin

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
