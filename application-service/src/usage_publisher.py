"""Host metering egress: Redis Stream implementation of the in-package UsagePublisher protocol.

XADD `model:usage` (single payload field = event JSON text), XTRIM capped at 500k;
publisher failures only log a warning (`publish_usage_safely` backstop) and never
block or roll back a business call.
"""

from __future__ import annotations

import json

from redbear_model import UsageEvent, UsagePublisher

from .aio_redis import get_thread_safe_sync_redis

MODEL_USAGE_STREAM = "model:usage"
MODEL_USAGE_STREAM_MAXLEN = 500_000


class RedisStreamPublisher(UsagePublisher):
    """Usage events -> the `model:usage` Stream (the `model-usage-consumers` group is created by the consuming side)."""

    def report_usage(self, event: UsageEvent) -> None:
        get_thread_safe_sync_redis().xadd(
            MODEL_USAGE_STREAM,
            {"payload": json.dumps(event.to_stream_dict(), ensure_ascii=False)},
            maxlen=MODEL_USAGE_STREAM_MAXLEN,
            approximate=True,
        )


usage_publisher = RedisStreamPublisher()
