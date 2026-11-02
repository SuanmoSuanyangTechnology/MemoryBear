"""Attribution headers shared by the HTTP route and the gRPC servicer."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from ..errors import BizCode
from .invoke_service import InvokeAttribution, InvokeFailure

RESOURCE_TYPE_HEADER = "X-Model-Resource-Type"
RESOURCE_ID_HEADER = "X-Model-Resource-ID"
SOURCE_REQUIRED_MESSAGE = "缺少来源标识 X-Model-Source（usage 归因必填）"


def attribution_from_headers(
    headers: Mapping[str, str],
    *,
    source: str | None,
    trace_id: str | None,
) -> InvokeAttribution:
    """Source is required; resource ids must be UUIDs; the trace id rides along."""
    resolved_source = (source or "").strip()
    if not resolved_source:
        raise InvokeFailure(
            code=BizCode.INVALID_PARAMETER, message=SOURCE_REQUIRED_MESSAGE
        )
    raw_resource_id = (headers.get(RESOURCE_ID_HEADER) or "").strip()
    resource_id: UUID | None = None
    if raw_resource_id:
        try:
            resource_id = UUID(raw_resource_id)
        except ValueError as exc:
            raise InvokeFailure(
                code=BizCode.INVALID_PARAMETER,
                message=f"{RESOURCE_ID_HEADER} 必须是 UUID",
            ) from exc
    resource_type = (headers.get(RESOURCE_TYPE_HEADER) or "").strip() or None
    return InvokeAttribution(
        source_service=resolved_source,
        request_id=trace_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )


__all__ = [
    "RESOURCE_ID_HEADER",
    "RESOURCE_TYPE_HEADER",
    "SOURCE_REQUIRED_MESSAGE",
    "attribution_from_headers",
]
