"""保留 Wiki 失败原因与上游原文，复用服务现有的凭据脱敏规则。"""

from __future__ import annotations

import json
import re
import traceback
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import StatementError

from ...logging import redact_for_log
from .config import WikiError

_FRAME_LIMIT = 24
_CREDENTIAL_KEY = re.compile(r"password|passwd|secret|token|api[_-]?key|authorization|cookie", re.I)
_UPSTREAM_FIELDS = (
    "remote_code",
    "http_status",
    "status_code",
    "request_id",
    "attempts",
    "channel_id",
    "sqlstate",
    "pgcode",
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "***" if _CREDENTIAL_KEY.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_for_log(value)


def diagnostic_json(value: dict) -> str:
    return json.dumps(_redact(value), ensure_ascii=False, default=str)


def _exception_detail(exc: BaseException) -> dict:
    detail = {
        "error_type": type(exc).__name__,
        "frames": [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)[-_FRAME_LIMIT:]
        ],
    }
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        detail["validation_errors"] = errors
        detail["error_message"] = "; ".join(
            f"{'.'.join(map(str, error['loc'])) or '<root>'}: {error['msg']} ({error['type']})"
            for error in errors
        )
    elif isinstance(exc, StatementError):
        # SQLAlchemy 的 str(exc) 会附带 SQL 与绑定值，只保留驱动报告的真实原因。
        detail["error_message"] = str(exc.orig) if exc.orig is not None else type(exc).__name__
        if exc.orig is not None:
            detail["driver_error_type"] = type(exc.orig).__name__
            sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            if sqlstate is not None:
                detail["sqlstate"] = sqlstate
    else:
        detail["error_message"] = str(exc) or type(exc).__name__
    if isinstance(exc, json.JSONDecodeError):
        detail["json_error"] = {"line": exc.lineno, "column": exc.colno, "position": exc.pos}
    for name in _UPSTREAM_FIELDS:
        value = getattr(exc, name, None)
        if value is not None:
            detail[name] = value
    message = getattr(exc, "message", None)
    if message is not None:
        detail["upstream_message"] = message
    body = getattr(exc, "body", None)
    if body is not None:
        detail["upstream_body"] = body
    response = getattr(exc, "response", None)
    if response is not None:
        detail["http_status"] = getattr(response, "status_code", None)
        try:
            detail["upstream_body"] = response.text
        except (AttributeError, RuntimeError):
            # 诊断阶段只读取已经接收的响应，不额外发起网络 IO。
            pass
        headers = getattr(response, "headers", {})
        for name in ("x-request-id", "x-trace-id", "request-id", "traceparent"):
            if headers.get(name):
                detail.setdefault("upstream_request_ids", {})[name] = headers[name]
    detail.update(
        {
            key: value
            for key, value in getattr(exc, "diagnostics", {}).items()
            if key not in {"error_type", "frames", "causes"}
        }
    )
    return detail


def exception_details(exc: BaseException) -> dict:
    """按异常链采集原因和堆栈；不记录局部变量、请求正文或请求头。"""
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(_exception_detail(current))
        driver = getattr(current, "orig", None)
        current = current.__cause__ or driver or current.__context__
    detail = chain[0]
    if len(chain) > 1:
        detail["causes"] = chain[1:]
    return _redact(detail)


def progress_details(error: WikiError) -> str:
    """进度保留具体原因与上游错误；完整模型输出和堆栈通过 task_id 查 Worker 日志。"""

    def compact(value):
        if isinstance(value, dict):
            return {
                key: compact(item)
                for key, item in value.items()
                if key not in {"frames", "raw_output"}
            }
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    return diagnostic_json(compact(error.diagnostics or exception_details(error)))
