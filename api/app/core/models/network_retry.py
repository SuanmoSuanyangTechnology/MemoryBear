import logging

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

# 仅匹配 requests 系网络异常：无 SDK 重试的路径（dashscope、jina rerank 等）抛出
# 的是这些类型；OpenAI/Ark/botocore 等 SDK 自带重试，耗尽后抛各自异常，不会触发外层重试。
NETWORK_RETRYABLE = (requests.ConnectionError, requests.Timeout)
NETWORK_RETRY_ATTEMPTS = settings.LLM_NETWORK_RETRY_ATTEMPTS
_retry_logger = logging.getLogger("business")


def _log_retry_attempt(retry_state):
    """与 tenacity before_sleep_log 同格式的日志回调（避免 LoggerProtocol 类型检查问题）。"""
    _retry_logger.warning(
        "Retrying %s in %s seconds as it raised %s",
        retry_state.fn,
        retry_state.next_action.sleep,
        retry_state.outcome.exception(),
    )


def network_retry(fn):
    """网络层错误重试：上游 SDK/tenacity 默认不重试 ConnectionError（连接中断）与 Timeout。"""
    return retry(
        reraise=True,
        stop=stop_after_attempt(NETWORK_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(NETWORK_RETRYABLE),
        before_sleep=_log_retry_attempt,
    )(fn)
