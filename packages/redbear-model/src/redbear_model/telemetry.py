"""Optional telemetry ports for model runtime events."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class UsagePublishStats:
    """进程内旁路计量计数（spec §13.2：publisher 失败仅本地计数 + 告警日志）。"""

    published: int
    failed: int


class _UsagePublishCounter:
    """线程安全进程内计数（worker 线程/事件循环线程都可能发射事件）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._published = 0
        self._failed = 0

    def record_published(self) -> None:
        with self._lock:
            self._published += 1

    def record_failed(self) -> None:
        with self._lock:
            self._failed += 1

    def snapshot(self) -> UsagePublishStats:
        with self._lock:
            return UsagePublishStats(published=self._published, failed=self._failed)

    def reset(self) -> None:
        with self._lock:
            self._published = 0
            self._failed = 0


_usage_publish_counter = _UsagePublishCounter()


def usage_publish_stats() -> UsagePublishStats:
    """读取旁路计量本地计数快照（只读，不重置）。"""
    return _usage_publish_counter.snapshot()


def reset_usage_publish_stats() -> None:
    """重置本地计数（测试/进程重建用）。"""
    _usage_publish_counter.reset()


def publish_usage_safely(publisher: UsagePublisher, event: UsageEvent) -> None:
    """旁路发布用量事件：任何异常仅本地计数 + 告警日志，绝不抛回业务调用（spec §13.2）。"""
    try:
        publisher.report_usage(event)
    except Exception:
        _usage_publish_counter.record_failed()
        stats = _usage_publish_counter.snapshot()
        logger.exception(
            "Usage publishing failed for event=%s provider=%s model=%s failed_total=%d published_total=%d",
            event.event_id,
            event.provider.value,
            event.model_name,
            stats.failed,
            stats.published,
        )
    else:
        _usage_publish_counter.record_published()
