"""模型用量事件宿主出口桥（spec §13.2）：旁路发射，绝不传播异常到业务调用。

宿主类出口（llm/embedding/rerank/generation 包装器）在成功/失败终态调用
`report_usage_success{,_async}` / `report_usage_failure{,_async}`；token/图片数
为 best-effort 提取（流式无聚合，记 0/NULL）。归属来自 RedBearModelConfig 的
tenant_id/model_config_id/channel_id（中央构建器填充），业务归因来自
`app.core.usage_context` 的 contextvar，request_id 复用请求 trace_id。
换渠道门面另报 attempts（provider 调用次数）与 fallback（换渠道后成功 →
FALLBACK_SUCCEEDED，spec §11.2），无 plan 路径维持 attempts=1 现状语义。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

from redbear_model import (
    ModelProvider,
    ModelType,
    UsageEvent,
    UsageStatus,
    publish_usage_safely,
)

from app.core.logging_config import get_logger
from app.core.trace import get_trace_id
from app.core.usage_context import get_usage_resource
from app.usage_publisher import usage_publisher

logger = get_logger(__name__)

SOURCE_SERVICE = "api"
_ATTEMPTS = 1


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text[:limit] or None


def _usage_mapping(obj: Any) -> Any:
    """响应对象 → usage 映射/对象（dict 键或属性），无则 None。"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        for key in ("usage_metadata", "usage", "token_usage"):
            value = obj.get(key)
            if value:
                return value
        return None
    for attr in ("usage_metadata", "usage", "token_usage"):
        value = getattr(obj, attr, None)
        if value:
            return value
    return None


def _token_pair(usage: Any) -> tuple[int, int]:
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        input_raw = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_raw = usage.get("output_tokens", usage.get("completion_tokens"))
    else:
        input_raw = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
        output_raw = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)

    def _as_int(value: Any) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0

    return _as_int(input_raw), _as_int(output_raw)


def _extract_tokens(result: Any) -> tuple[int, int]:
    """best-effort：AIMessage / LLMResult / ChatGeneration / 原生响应 → (input, output)。"""
    usage = _usage_mapping(result)
    if usage is None:
        llm_output = result.get("llm_output") if isinstance(result, dict) else getattr(result, "llm_output", None)
        usage = _usage_mapping(llm_output)
    if usage is None:
        response_metadata = getattr(result, "response_metadata", None)
        if isinstance(response_metadata, dict):
            usage = _usage_mapping(response_metadata)
    if usage is None:
        generations = result.get("generations") if isinstance(result, dict) else getattr(result, "generations", None)
        if isinstance(generations, (list, tuple)) and generations:
            first = generations[0]
            generation = first[0] if isinstance(first, (list, tuple)) and first else first
            usage = _usage_mapping(getattr(generation, "message", None)) or _usage_mapping(generation)
    return _token_pair(usage)


def _extract_images(operation: str, result: Any) -> int | None:
    """图片生成按响应 data 长度记张数（流式响应为迭代器，不计）。"""
    if operation != "image.generate" or result is None:
        return None
    data = result.get("data") if isinstance(result, dict) else getattr(result, "data", None)
    if isinstance(data, (list, tuple)) and data:
        return len(data)
    return None


def _emit(
    config: Any,
    capability: str,
    operation: str,
    started_at: float,
    *,
    status: UsageStatus,
    exc: BaseException | None = None,
    stream: bool = False,
    result: Any = None,
    attempts: int = _ATTEMPTS,
    fallback: bool = False,
) -> None:
    try:
        tenant_id = _as_uuid(getattr(config, "tenant_id", None))
        config_id = _as_uuid(getattr(config, "model_config_id", None))
        if tenant_id is None or config_id is None:
            logger.debug(
                "用量事件跳过（缺归属）: provider=%s model=%s tenant=%s config=%s",
                getattr(config, "provider", None),
                getattr(config, "model_name", None),
                tenant_id,
                config_id,
            )
            return
        try:
            provider = ModelProvider(str(getattr(config, "provider", "")))
            capability_value = ModelType(capability)
        except ValueError:
            logger.debug(
                "用量事件跳过（未知 provider/capability）: provider=%s capability=%s",
                getattr(config, "provider", None),
                capability,
            )
            return
        if fallback and status is UsageStatus.OK:
            status = UsageStatus.FALLBACK_SUCCEEDED
        input_tokens, output_tokens = _extract_tokens(result) if result is not None else (0, 0)
        resource = get_usage_resource()
        try:
            latency_ms = max(0, int((time.perf_counter() - float(started_at)) * 1000))
        except (TypeError, ValueError):
            latency_ms = 0
        event = UsageEvent(
            source_service=SOURCE_SERVICE,
            tenant_id=tenant_id,
            config_id=config_id,
            channel_id=_as_uuid(getattr(config, "channel_id", None)),
            provider=provider,
            model_name=_truncate(getattr(config, "model_name", None), 255) or "unknown",
            capability=capability_value,
            stream=stream,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            images_count=_extract_images(operation, result),
            latency_ms=latency_ms,
            status=status,
            error_type=None if exc is None else _truncate(type(exc).__name__, 64),
            attempts=max(1, attempts),
            request_id=_truncate(get_trace_id(), 64),
            resource_type=None if resource is None else _truncate(resource.resource_type, 32),
            resource_id=None if resource is None else _as_uuid(resource.resource_id),
        )
        publish_usage_safely(usage_publisher, event)
    except Exception as emit_exc:
        # 旁路铁律：发射侧任何异常（含事件校验失败）不得影响业务调用
        logger.warning("用量事件发射失败: %s", type(emit_exc).__name__, exc_info=True)


def report_usage_success(
    config: Any,
    capability: str,
    operation: str,
    started_at: float,
    *,
    stream: bool = False,
    result: Any = None,
    attempts: int = _ATTEMPTS,
    fallback: bool = False,
) -> None:
    _emit(
        config, capability, operation, started_at,
        status=UsageStatus.OK, stream=stream, result=result,
        attempts=attempts, fallback=fallback,
    )


def report_usage_failure(
    config: Any,
    capability: str,
    operation: str,
    exc: BaseException,
    started_at: float,
    *,
    stream: bool = False,
    attempts: int = _ATTEMPTS,
) -> None:
    _emit(
        config, capability, operation, started_at,
        status=UsageStatus.FAILED, exc=exc, stream=stream, attempts=attempts,
    )


async def report_usage_success_async(
    config: Any,
    capability: str,
    operation: str,
    started_at: float,
    *,
    stream: bool = False,
    result: Any = None,
    attempts: int = _ATTEMPTS,
    fallback: bool = False,
) -> None:
    await asyncio.to_thread(
        report_usage_success, config, capability, operation, started_at,
        stream=stream, result=result, attempts=attempts, fallback=fallback,
    )


async def report_usage_failure_async(
    config: Any,
    capability: str,
    operation: str,
    exc: BaseException,
    started_at: float,
    *,
    stream: bool = False,
    attempts: int = _ATTEMPTS,
) -> None:
    await asyncio.to_thread(
        report_usage_failure, config, capability, operation, exc, started_at,
        stream=stream, attempts=attempts,
    )
