"""熔断器：连续失败超阈值短路 N 秒，期间直接拒绝新请求（不影响已建立 SSE 流）。"""
from __future__ import annotations

import threading
import time


class CircuitBreaker:
    def __init__(self, fail_threshold: int = 10, window_seconds: float = 30,
                 open_seconds: float = 5) -> None:
        self._fail_threshold = fail_threshold
        self._window_seconds = window_seconds
        self._open_seconds = open_seconds
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if time.monotonic() - self._opened_at > self._open_seconds:
                self._opened_at = None
                self._failures.clear()
                return False
            return True

    def record_failure(self) -> None:
        with self._lock:
            if self._opened_at is not None:
                return
            now = time.monotonic()
            self._failures = [t for t in self._failures if now - t < self._window_seconds]
            self._failures.append(now)
            if len(self._failures) >= self._fail_threshold:
                self._opened_at = now
                self._failures.clear()

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
