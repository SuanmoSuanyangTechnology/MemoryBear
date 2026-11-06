"""Client-side channel pooling and RPC metrics.

One channel per target per process; gRPC channels are multiplexed and
thread-safe, so a pool avoids rebuilding subchannel and resolver state per
call. DNS targets use round_robin so every headless-Service pod receives
streams. Async pools are loop-bound; the sync twin is process-level because
sync channels are safe to share across threads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import grpc
from prometheus_client import Counter

logger = logging.getLogger(__name__)

ROUND_ROBIN_SERVICE_CONFIG: dict[str, Any] = {
    "loadBalancingConfig": [{"round_robin": {}}]
}

CLIENT_FRAMES = Counter(
    "grpc_client_frames_total",
    "Frames received from gRPC servers, by target and frame kind.",
    ("target", "kind"),
)
STREAM_CANCELLED = Counter(
    # Distinct from the server-side grpc_stream_cancelled_total:
    # prometheus_client rejects duplicate collector names with different
    # label sets.
    "grpc_client_stream_cancelled_total",
    "Client-side streams terminated by cancellation, by target.",
    ("target", "role"),
)


class ChannelPool:
    def __init__(
        self,
        *,
        max_message_bytes: int,
        keepalive_time_ms: int,
        keepalive_timeout_ms: int,
        service_config: dict[str, Any] | None = None,
    ) -> None:
        self._options = (
            ("grpc.max_receive_message_length", max_message_bytes),
            ("grpc.max_send_message_length", max_message_bytes),
            ("grpc.keepalive_time_ms", keepalive_time_ms),
            ("grpc.keepalive_timeout_ms", keepalive_timeout_ms),
            ("grpc.keepalive_permit_without_calls", 1),
            (
                "grpc.service_config",
                json.dumps(service_config or ROUND_ROBIN_SERVICE_CONFIG),
            ),
        )
        self._channels: dict[str, grpc.aio.Channel] = {}
        self._closed = False
        self._lock = asyncio.Lock()

    async def channel(self, target: str) -> grpc.aio.Channel:
        channel = self._channels.get(target)
        if channel is not None:
            return channel
        async with self._lock:
            if self._closed:
                raise RuntimeError("gRPC channel pool is closed")
            channel = self._channels.get(target)
            if channel is None:
                channel = grpc.aio.insecure_channel(target, options=self._options)
                self._channels[target] = channel
                logger.info("grpc_channel_created target=%s", target)
            return channel

    async def aclose(self, grace_s: float = 5.0) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            channels = tuple(self._channels.items())
            self._channels.clear()
        for target, channel in channels:
            try:
                await channel.close(grace_s)
            except Exception:  # noqa: BLE001 - shutdown must not mask the first error
                logger.exception("grpc_channel_close_failed target=%s", target)


class SyncChannelPool:
    """Synchronous twin of :class:`ChannelPool` for blocking call sites.

    Sync channels are thread-safe and not loop-bound, so one per target per
    process is enough; the pool keeps round_robin subchannel/resolver state warm
    across calls. The caller owns closing.
    """

    def __init__(
        self,
        *,
        max_message_bytes: int,
        keepalive_time_ms: int,
        keepalive_timeout_ms: int,
        service_config: dict[str, Any] | None = None,
    ) -> None:
        self._options = (
            ("grpc.max_receive_message_length", max_message_bytes),
            ("grpc.max_send_message_length", max_message_bytes),
            ("grpc.keepalive_time_ms", keepalive_time_ms),
            ("grpc.keepalive_timeout_ms", keepalive_timeout_ms),
            ("grpc.keepalive_permit_without_calls", 1),
            (
                "grpc.service_config",
                json.dumps(service_config or ROUND_ROBIN_SERVICE_CONFIG),
            ),
        )
        self._channels: dict[str, grpc.Channel] = {}
        self._closed = False
        self._lock = threading.Lock()

    def channel(self, target: str) -> grpc.Channel:
        channel = self._channels.get(target)
        if channel is not None:
            return channel
        with self._lock:
            if self._closed:
                raise RuntimeError("gRPC sync channel pool is closed")
            channel = self._channels.get(target)
            if channel is None:
                channel = grpc.insecure_channel(target, options=self._options)
                self._channels[target] = channel
                logger.info("grpc_sync_channel_created target=%s", target)
            return channel

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            channels = tuple(self._channels.items())
            self._channels.clear()
        for target, channel in channels:
            try:
                channel.close()
            except Exception:  # noqa: BLE001 - shutdown must not mask the first error
                logger.exception("grpc_sync_channel_close_failed target=%s", target)


__all__ = [
    "CLIENT_FRAMES",
    "ROUND_ROBIN_SERVICE_CONFIG",
    "STREAM_CANCELLED",
    "ChannelPool",
    "SyncChannelPool",
]
