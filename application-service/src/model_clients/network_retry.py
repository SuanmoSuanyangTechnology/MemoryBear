import logging

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import current_settings

# Matches only requests-based network errors: paths without SDK retries (dashscope, jina
# rerank, etc.) raise these types; OpenAI/Ark/botocore SDKs retry internally and raise
# their own exceptions once exhausted, never triggering this outer retry.
NETWORK_RETRYABLE = (requests.ConnectionError, requests.Timeout)
NETWORK_RETRY_ATTEMPTS = current_settings().llm_network_retry_attempts
_retry_logger = logging.getLogger("business")


def _log_retry_attempt(retry_state):
    """Log callback with the same format as tenacity before_sleep_log (avoids LoggerProtocol type-check issues)."""
    _retry_logger.warning(
        "Retrying %s in %s seconds as it raised %s",
        retry_state.fn,
        retry_state.next_action.sleep,
        retry_state.outcome.exception(),
    )


def network_retry(fn):
    """Retry network-layer errors: upstream SDKs/tenacity do not retry ConnectionError (broken connection) or Timeout by default."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(NETWORK_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(NETWORK_RETRYABLE),
        before_sleep=_log_retry_attempt,
    )(fn)
