"""Host metering egress: Redis Stream implementation of the in-package UsagePublisher protocol.

XADD to the deployment-scoped usage stream (``model:usage:<DB_NAME>`` by default,
overridable via ``MODEL_USAGE_STREAM``), capped at 500k entries. Publisher failures
only log a warning (`publish_usage_safely` backstop) and never block or roll back a
business call.
"""

from __future__ import annotations

import json

from redbear_model import UsageEvent, UsagePublisher

from .aio_redis import get_thread_safe_sync_redis
from .config import current_settings

MODEL_USAGE_STREAM_MAXLEN = 500_000


def model_usage_stream() -> str:
    """Return the deployment-scoped usage stream name."""

    return current_settings().model_usage_stream_name


class RedisStreamPublisher(UsagePublisher):
    """Usage events -> the deployment-scoped Stream.

    Consumer groups are created on the consuming side.
    """

    def report_usage(self, event: UsageEvent) -> None:
        get_thread_safe_sync_redis().xadd(
            model_usage_stream(),
            {"payload": json.dumps(event.to_stream_dict(), ensure_ascii=False)},
            maxlen=MODEL_USAGE_STREAM_MAXLEN,
            approximate=True,
        )


usage_publisher = RedisStreamPublisher()
