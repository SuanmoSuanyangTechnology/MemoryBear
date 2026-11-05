"""负责新模型的 HTTP 请求和结果校验。

本文件不处理 LLM 回退；发生错误时，由调用方执行原有 LLM 方法。
并发由业务调用方管理；本文件按环境配置控制单次调用总超时。
每次调用单独创建 HTTP 客户端，并在结束时关闭。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

import httpx
from pydantic import BaseModel, ConfigDict

from app.core.config import settings

logger = logging.getLogger(__name__)
ASSISTANT_TYPES = frozenset({
    "comfort", "suggestion", "recommendation", "warning", "instruction",
    "question", "agreement", "repetition", "other",
})


class ExtractPruningResult(BaseModel):
    """五个字段均为必填且严格检查类型；与输入相关的规则由下方函数校验。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    assistant_memory_hint: str | None
    assistant_memory_type: str | None
    should_process_user_msg: bool | None
    processed_user_msg: str | None
    processed_user_topic_entity_hint: str | None


def validate_result(data: object, user_text: str, assistant_text: str) -> ExtractPruningResult:
    result = ExtractPruningResult.model_validate(data)

    def generated(value: str | None) -> bool:
        return value is not None and bool(value.strip()) and value.strip().lower() != "null"

    if assistant_text.strip():
        if not generated(result.assistant_memory_hint) or result.assistant_memory_type not in ASSISTANT_TYPES:
            raise ValueError("invalid assistant result")
    elif result.assistant_memory_hint is not None or result.assistant_memory_type is not None:
        raise ValueError("empty assistant must have null fields")

    user_fields = (result.processed_user_msg, result.processed_user_topic_entity_hint)
    if not user_text.strip():
        if result.should_process_user_msg is not None or any(value is not None for value in user_fields):
            raise ValueError("empty user must have null fields")
    elif result.should_process_user_msg is True:
        if not all(generated(value) for value in user_fields):
            raise ValueError("processed user requires text and topic")
    elif result.should_process_user_msg is False:
        if any(value is not None for value in user_fields):
            raise ValueError("unprocessed user must have null generated fields")
    else:
        raise ValueError("nonempty user requires boolean gate")
    return result


def is_available() -> bool:
    return bool(
        settings.EXTRACT_PRUNING_ENABLED
        and settings.EXTRACT_PRUNING_URL.strip()
        and settings.EXTRACT_PRUNING_API_KEY.strip()
        and settings.EXTRACT_PRUNING_MODEL.strip()
    )


async def predict(user_text: str, assistant_text: str) -> ExtractPruningResult:
    """只调用一次，不重试；异常和取消继续向上传递，客户端在结束时关闭。"""
    if not is_available():
        raise ValueError("extract-pruning is disabled or unconfigured")
    if not user_text.strip() and not assistant_text.strip():
        return validate_result({name: None for name in ExtractPruningResult.model_fields}, "", "")

    started = time.monotonic()
    try:
        timeout = settings.EXTRACT_PRUNING_REQUEST_TIMEOUT_SECONDS
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("request timeout must be finite and positive")
        # 统一控制单次调用总超时；客户端不管理业务并发或本地排队。
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(
                    settings.EXTRACT_PRUNING_URL,
                    headers={
                        "Authorization": f"Bearer {settings.EXTRACT_PRUNING_API_KEY}",
                        "X-GPUStack-Model": settings.EXTRACT_PRUNING_MODEL,
                    },
                    json={"msgs": [
                        {"role": "User", "msg": user_text},
                        {"role": "Assistant", "msg": assistant_text},
                    ]},
                )
                response.raise_for_status()
                result = validate_result(response.json(), user_text, assistant_text)
    finally:
        logger.info("[ExtractPruning] elapsed=%.3fs", time.monotonic() - started)
    return result
