"""Optional telemetry ports for model runtime events."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from .contracts import ResolvedModelConfig
from .usage import UsageEvent

logger = logging.getLogger(__name__)
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


class ModelTelemetry(Protocol):
    def report_failure(
        self,
        *,
        provider: str,
        model_name: str,
        operation: str,
        error_type: str,
        latency_ms: float,
    ) -> None: ...


class NoOpModelTelemetry:
    def report_failure(
        self,
        *,
        provider: str,
        model_name: str,
        operation: str,
        error_type: str,
        latency_ms: float,
    ) -> None:
        return None


class UsagePublisher(Protocol):
    """旁路计量出口（spec §13.2）：宿主注册 RedisStreamPublisher（XADD model:usage）。

    计量是旁路——publisher 失败仅告警，绝不阻塞/回滚业务调用。
    """

    def report_usage(self, event: UsageEvent) -> None: ...


class NoOpUsagePublisher:
    def report_usage(self, event: UsageEvent) -> None:
        return None


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
    """Identify connectivity, authentication, and server-side failures."""
    current: BaseException | None = exc
    for _ in range(4):
        if current is None:
            break
        if isinstance(
            current,
            (
                TimeoutError,
                ConnectionError,
            ),
        ):
            return True
        status = _status_code(current)
        if status in {401, 403} or (status is not None and status >= 500):
            return True
        name = type(current).__name__.lower()
        if any(token in name for token in _GATEWAY_ERROR_NAMES):
            return True
        current = current.__cause__ or current.__context__
    return False


def report_failure_safely(
    telemetry: ModelTelemetry,
    config: ResolvedModelConfig,
    *,
    operation: str,
    exc: BaseException,
    started_at: float,
) -> None:
    """Report a redacted failure without replacing the provider exception."""
    if not is_model_gateway_failure(exc):
        return
    try:
        telemetry.report_failure(
            provider=config.provider.value,
            model_name=config.model_name,
            operation=operation,
            error_type=type(exc).__name__,
            latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
    except Exception:
        logger.exception(
            "Model telemetry reporting failed for provider=%s model=%s operation=%s",
            config.provider.value,
            config.model_name,
            operation,
        )


def publish_usage_safely(publisher: UsagePublisher, event: UsageEvent) -> None:
    """旁路发布用量事件：任何异常仅告警日志，绝不抛回业务调用（spec §13.2）。"""
    try:
        publisher.report_usage(event)
    except Exception:
        logger.exception(
            "Usage publishing failed for event=%s provider=%s model=%s",
            event.event_id,
            event.provider.value,
            event.model_name,
        )
