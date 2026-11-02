"""grpc.aio server assembly shared by application-service and model-service."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc


def assert_asyncio_loop() -> None:
    """grpc.aio does not support uvloop; fail loudly instead of degrading."""

    loop = asyncio.get_running_loop()
    if type(loop).__module__.startswith("uvloop"):
        raise RuntimeError(
            "uvloop is incompatible with grpc.aio; start uvicorn with --loop asyncio"
        )


class GrpcServer:
    def __init__(
        self,
        *,
        server: grpc.aio.Server,
        port: int,
        health_servicer: health.aio.HealthServicer,
        health_name: str,
    ) -> None:
        self._server = server
        self._port = port
        self._health = health_servicer
        self._health_name = health_name

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        await self._health.set(
            self._health_name, health_pb2.HealthCheckResponse.SERVING
        )
        await self._server.start()

    async def stop(self, *, grace_s: float) -> None:
        await self._health.set(
            self._health_name, health_pb2.HealthCheckResponse.NOT_SERVING
        )
        await self._server.stop(grace_s)


def create_grpc_server(
    *,
    service_name: str,
    port: int,
    register: Callable[[grpc.aio.Server], None],
    max_message_bytes: int,
    keepalive_time_ms: int,
    keepalive_timeout_ms: int,
    max_concurrent_rpcs: int,
    interceptors: Sequence[grpc.aio.ServerInterceptor] = (),
    health_name: str = "",
) -> GrpcServer:
    """Build the grpc.aio server with health checking and an interceptor chain.

    Interceptors run in list order with the first entry outermost. Place
    observability (``MetricsInterceptor``/``LoggingInterceptor``) outside
    ``AuthInterceptor`` so auth aborts and rejections are still recorded.
    """
    assert_asyncio_loop()
    options = (
        ("grpc.max_receive_message_length", max_message_bytes),
        ("grpc.max_send_message_length", max_message_bytes),
        ("grpc.keepalive_time_ms", keepalive_time_ms),
        ("grpc.keepalive_timeout_ms", keepalive_timeout_ms),
        ("grpc.keepalive_permit_without_calls", 1),
        # grpc-core treats pings without data as abuse (5-minute min interval,
        # 2 strikes) and answers with a too_many_pings GOAWAY that kills
        # in-flight RPCs. Our clients ping at the configured keepalive cadence
        # and the mesh is NetworkPolicy-bounded, so disable the detector.
        ("grpc.http2.max_ping_strikes", 0),
    )
    server = grpc.aio.server(
        options=options,
        interceptors=tuple(interceptors),
        maximum_concurrent_rpcs=max_concurrent_rpcs,
    )
    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    register(server)
    bound = server.add_insecure_port(f"0.0.0.0:{port}")
    if bound == 0:
        raise RuntimeError(f"{service_name}: failed to bind gRPC port {port}")
    return GrpcServer(
        server=server,
        port=bound,
        health_servicer=health_servicer,
        health_name=health_name,
    )


__all__ = ["GrpcServer", "assert_asyncio_loop", "create_grpc_server"]
