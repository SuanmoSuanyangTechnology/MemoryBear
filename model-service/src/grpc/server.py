"""Build the model-service gRPC listener (Invoke service + health)."""

from __future__ import annotations

from memorybear.model.v1 import invoke_pb2_grpc
from memorybear_grpc.interceptors import (
    AuthInterceptor,
    LoggingInterceptor,
    MetricsInterceptor,
    TraceInterceptor,
)
from memorybear_grpc.server import GrpcServer, create_grpc_server

import grpc

from ..auth import ModelAuthConfig
from ..config import ModelServiceSettings
from ..runtime import ProcessRuntime
from ..trace import reset_trace_id, set_trace_id
from .auth import build_authenticator
from .invoke_servicer import InvokeServicer


def build_grpc_server(
    settings: ModelServiceSettings,
    *,
    runtime: ProcessRuntime | None = None,
    auth_config: ModelAuthConfig | None = None,
    gateway: object | None = None,
) -> GrpcServer | None:
    if not settings.grpc_server_enabled:
        return None
    if runtime is None:
        raise ValueError("the model-service gRPC server requires the process runtime")
    if auth_config is None:
        raise ValueError("the model-service gRPC server requires the auth config")
    authenticate = build_authenticator(auth_config, gateway=gateway)

    def register(server: grpc.aio.Server) -> None:
        invoke_pb2_grpc.add_ModelInvokeServiceServicer_to_server(
            InvokeServicer(runtime), server
        )

    return create_grpc_server(
        service_name=settings.service_name,
        port=settings.grpc_port,
        register=register,
        max_message_bytes=settings.grpc_max_message_bytes,
        keepalive_time_ms=settings.grpc_keepalive_time_ms,
        keepalive_timeout_ms=settings.grpc_keepalive_timeout_ms,
        max_concurrent_rpcs=settings.grpc_max_concurrent_rpcs,
        # First entry is outermost: observability must wrap auth so rejections
        # and aborts are counted and logged.
        interceptors=(
            MetricsInterceptor(settings.service_name),
            LoggingInterceptor(settings.service_name),
            TraceInterceptor(bind=set_trace_id, reset=reset_trace_id),
            AuthInterceptor(authenticate),
        ),
    )


__all__ = ["build_grpc_server"]
