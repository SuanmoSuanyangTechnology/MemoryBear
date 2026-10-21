"""Host egress bridge for model usage events: side-channel emission that never propagates an exception into the business call.

Host-facing outlets (llm/embedding/rerank/generation wrappers) call
`report_usage_success{,_async}` / `report_usage_failure{,_async}` at a success/failure
terminal state; token/image counts are extracted best-effort (no aggregation for
streams -- recorded as 0/NULL). Ownership comes from RedBearModelConfig's
tenant_id/model_config_id/channel_id (filled by the central builder), business
attribution comes from the `app.core.usage_context` contextvar, and request_id reuses
the request trace_id. The channel-switch facade additionally reports attempts
(provider call count) and fallback (success after a channel switch ->
FALLBACK_SUCCEEDED); paths without a plan keep the current attempts=1 semantics.
"""

from __future__ import annotations

import logging

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

from .trace import get_trace_id
from .usage_context import get_usage_resource
from .usage_publisher import usage_publisher

logger = logging.getLogger(__name__)

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
    """Response object -> usage mapping/object (dict key or attribute); None when absent."""
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
    """Best-effort: AIMessage / LLMResult / ChatGeneration / native response -> (input, output)."""
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
    """For image generation, count images from the response data length (streaming responses are iterators and are not counted)."""
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
        # Side-channel invariant: no emit-side exception (including event validation
        # failures) may affect the business call
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
