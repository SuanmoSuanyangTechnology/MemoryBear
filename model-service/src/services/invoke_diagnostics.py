"""只观察服务与供应商已有的失败位置，不读取或包装 HTTP 响应。"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import traceback
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import APIStatusError
from redbear_model import ResolvedModelConfig
from redbear_model.telemetry import provider_failure_scope

from ..errors import BizCode
from ..logging import log_diagnostic, redact_for_log
from ..request_logging import _exception_nodes, _safe_exception_chain

logger = logging.getLogger(__name__)
_current: ContextVar[InvokeDiagnostics | None] = ContextVar("invoke_diagnostics", default=None)
_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-amzn-requestid",
    "x-amz-request-id",
    "x-ms-request-id",
)


@dataclass
class InvokeDiagnostics:
    fields: dict[str, Any]
    started: float = field(default_factory=time.perf_counter)
    candidate: dict[str, Any] = field(default_factory=dict)
    last_provider_failure: dict[str, Any] | None = None
    stage: str = "resolve"
    secrets: set[str] = field(default_factory=set, repr=False)

    def text(self, value: object) -> str:
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return redact_for_log(text)

    def _is_input_failure(self, exc: BaseException) -> bool:
        return self.stage == "tool_prepare" or (
            self.stage == "resolve" and getattr(exc, "code", None) is BizCode.INVALID_PARAMETER
        )

    def failure_message(self, exc: BaseException) -> str:
        if self._is_input_failure(exc):
            return f"{type(exc).__name__}（{self.stage}，请求值未记录）"
        return self.text(exc)

    def exception(self, exc: BaseException) -> dict[str, Any]:
        if self._is_input_failure(exc):
            # 预校验异常可能回显完整请求值；复用现有的字段、类型和帧位置摘要。
            code = getattr(exc, "code", None)
            return {
                "error_type": type(exc).__name__,
                "error_code": code.name if isinstance(code, BizCode) else "TOOL_PREPARATION_FAILED",
                "exception_chain": _safe_exception_chain(_exception_nodes(exc)),
            }
        chain = []
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            node = {
                "error_type": type(current).__name__,
                "error_message": str(current),
                "stack": "".join(
                    traceback.format_exception(
                        type(current),
                        current,
                        current.__traceback__,
                        chain=False,
                    )
                ),
            }
            response = getattr(current, "response", None)
            request = getattr(current, "request", None)
            for attribute, output in (
                ("status_code", "upstream_status"),
                ("provider_code", "upstream_error_code"),
                ("provider_request_id", "upstream_request_id"),
            ):
                value = getattr(current, attribute, None)
                if value is not None:
                    node[output] = value
            if response is not None and not isinstance(response, Mapping):
                node["upstream_status"] = response.status_code
                node["upstream_request_id"] = next(
                    (response.headers[h] for h in _REQUEST_ID_HEADERS if response.headers.get(h)),
                    "",
                )
            if request is not None:
                node["upstream_url"] = str(request.url)
            # 只取 SDK 已经提供的数据；content 属性不会触发 HTTPX 网络读取。
            if isinstance(response, httpx.Response):
                try:
                    node["upstream_response"] = response.content.decode("utf-8", errors="replace")
                except httpx.ResponseNotRead:
                    node["upstream_response_available"] = False
            elif isinstance(current, APIStatusError):
                node["upstream_response"] = current.body
            elif isinstance(response, Mapping):
                node["upstream_response"] = dict(response)
            chain.append(node)
            current = current.__cause__ or (
                None if current.__suppress_context__ else current.__context__
            )
        return {"error_type": type(exc).__name__, "exception_chain": chain}

    def emit(
        self,
        event: str,
        level: int = logging.INFO,
        *,
        error: BaseException | None = None,
        **fields: Any,
    ) -> None:
        try:
            details = self.exception(error) if error is not None else None
            if details is not None and event in {"model_sdk_failed", "model_provider_failed"}:
                self.last_provider_failure = {
                    "event": event,
                    "stage": self.stage,
                    **self.candidate,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                }
            payload = {
                "event": event,
                **self.fields,
                **self.candidate,
                "stage": self.stage,
                "duration_ms": int((time.perf_counter() - self.started) * 1000),
                **fields,
            }
            if details is not None:
                payload["error"] = details
            if level >= logging.WARNING:
                payload["last_provider_failure"] = self.last_provider_failure
            log_diagnostic(logger, level, payload, secrets=self.secrets)
        except Exception:
            # 诊断只能追加信息，不能替换业务结果或原始异常。
            return

    def start_candidate(self, resolved: ResolvedModelConfig, attempt_no: int) -> None:
        secret = resolved.api_key.get_secret_value()
        if secret:
            self.secrets.add(secret)
        self.candidate = {
            "actual_config_id": str(resolved.model_config_id),
            "provider": resolved.provider.value,
            "model_name": resolved.model_name,
            "channel_id": str(resolved.channel_id) if resolved.channel_id else None,
            "base_url": resolved.base_url,
            "attempt_no": attempt_no,
        }
        self.stage = "provider_call"
        self.emit("model_attempt_started")

    def provider_failure(
        self,
        config: ResolvedModelConfig,
        *,
        operation: str,
        upstream_url: str,
        status_code: int | None,
        response_body: str | None,
        error: BaseException,
    ) -> None:
        self.emit(
            "model_provider_failed",
            logging.WARNING,
            error=error,
            provider_operation=operation,
            actual_config_id=str(config.model_config_id),
            provider=config.provider.value,
            model_name=config.model_name,
            channel_id=str(config.channel_id) if config.channel_id else None,
            upstream_url=upstream_url,
            upstream_status=status_code,
            upstream_response=response_body,
            upstream_response_available=response_body is not None,
        )


@dataclass
class DiagnosticCall:
    call: Callable[[], Awaitable[Any]]
    diagnostics: InvokeDiagnostics

    async def __call__(self) -> Any:
        token = _current.set(self.diagnostics)
        try:
            with provider_failure_scope(self.diagnostics.provider_failure):
                self.diagnostics.emit("model_invoke_started")
                try:
                    return await self.call()
                except asyncio.CancelledError:
                    self.diagnostics.emit("model_invoke_cancelled")
                    raise
                except Exception as exc:
                    self.diagnostics.emit("model_invoke_failed", logging.ERROR, error=exc)
                    raise
        finally:
            _current.reset(token)


def current_diagnostics() -> InvokeDiagnostics | None:
    return _current.get()


class SDKFailureFilter(logging.Filter):
    """仅接收锁定 OpenAI SDK 的原生失败日志，抑制请求参数等 DEBUG 内容。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.INFO:
            return True
        if isinstance(record.msg, str) and record.msg.startswith("Encountered "):
            diagnostics = current_diagnostics()
            # 新旧 SDK 分别使用 exc_info 和当前 except 上下文提供原始异常。
            error = record.exc_info[1] if record.exc_info else sys.exception()
            if diagnostics is not None and error is not None:
                diagnostics.emit("model_sdk_failed", logging.WARNING, error=error)
        return False


def install_sdk_failure_logging() -> None:
    sdk_logger = logging.getLogger("openai._base_client")
    if not any(isinstance(item, SDKFailureFilter) for item in sdk_logger.filters):
        sdk_logger.addFilter(SDKFailureFilter())
    sdk_logger.setLevel(logging.DEBUG)
