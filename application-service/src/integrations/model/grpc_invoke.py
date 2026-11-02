"""Host-facing gRPC invoke transport: the package transport plus host vocabulary.

The redbear transport already owns frame semantics; this layer only translates
``RemoteInvoke*`` failures into the integration layer's error types, mirroring
``HostAsyncInvokeTransport`` for the HTTP path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from redbear_model.errors import RemoteInvokeError
from redbear_model.runtime.grpc_remote import GrpcAsyncInvokeTransport
from redbear_model.runtime.remote import InvokeFrame, InvokeRequest, InvokeTarget

from .invoke import to_host_error


class HostGrpcAsyncInvokeTransport(GrpcAsyncInvokeTransport):
    """True subclass on purpose: the in-package chat adapter selects transport flavor via isinstance."""

    async def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return await super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc

    async def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> AsyncIterator[InvokeFrame]:
        try:
            async for frame in super().stream(request, target):
                yield frame
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc


__all__ = ["HostGrpcAsyncInvokeTransport"]
