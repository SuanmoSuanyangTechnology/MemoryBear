"""Build the mem-knowledge gRPC listener (retrieval service + health)."""

from __future__ import annotations

from memorybear.knowledge.v1 import retrieval_pb2_grpc
from memorybear_grpc.interceptors import (
    AuthInterceptor,
    LoggingInterceptor,
    MetricsInterceptor,
    TraceInterceptor,
)
from memorybear_grpc.server import GrpcServer, create_grpc_server

import grpc

from ..auth import KbAuthConfig
from ..config import KnowledgeSettings
from ..runtime import ProcessRuntime
from ..trace import reset_trace_id, set_trace_id
from .auth import build_authenticator
from .retrieval_servicer import KnowledgeRetrievalServicer


def build_grpc_server(
    settings: KnowledgeSettings,
    runtime: ProcessRuntime,
    *,
    auth_config: KbAuthConfig | None = None,
    gateway: object | None = None,
) -> GrpcServer | None:
    if not settings.grpc_server_enabled:
        return None
    if auth_config is None:
        raise ValueError("the mem-knowledge gRPC server requires the auth config")
    authenticate = build_authenticator(auth_config, gateway=gateway)

    def register(server: grpc.aio.Server) -> None:
        retrieval_pb2_grpc.add_KnowledgeRetrievalServiceServicer_to_server(
            KnowledgeRetrievalServicer(runtime), server
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