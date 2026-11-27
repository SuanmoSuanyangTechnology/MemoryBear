"""Delegating facade for the workspace memory-config resolver.

The active memory configuration id belongs to memory-profile; during the
coexistence period this service reads it through the internal
``memory_config/workspace_active_config`` route with the end user acting as
the internal principal. An unconfigured channel fails closed, and upstream
rejections (for example a workspace without an enabled memory config) surface
with the upstream error message and code.
"""

import logging
import uuid

from ..errors import BizCode, BusinessException, as_biz_code
from ..integrations.memory_profile import (
    MemoryProfileProtocolError,
    MemoryProfileUnavailableError,
    get_memory_profile_client,
)
from ..memory.memory_service import resolve_internal_identity
from ..trace import get_trace_id

logger = logging.getLogger(__name__)

_UNAVAILABLE_MESSAGE = (
    "The active memory configuration is served by memory-profile and its "
    "channel is not configured (MEMORY_PROFILE_BASE_URL)"
)


class MemoryConfigService:
    """Monolith-compatible facade backed by memory-profile's internal route."""

    async def get_workspace_active_config_id_async(
            self,
            workspace_id: uuid.UUID,
            end_user_id: str,
    ) -> uuid.UUID:
        client = get_memory_profile_client()
        if client is None or not client.configured:
            raise BusinessException(_UNAVAILABLE_MESSAGE, code=BizCode.SERVICE_UNAVAILABLE)
        actor_id, tenant_id, workspace_uuid = await resolve_internal_identity(
            end_user_id, str(workspace_id)
        )
        try:
            envelope = await client.fetch_workspace_active_config(
                workspace_id=workspace_uuid,
                tenant_id=tenant_id,
                actor_id=actor_id,
                trace_id=get_trace_id() or uuid.uuid4().hex,
            )
        except MemoryProfileUnavailableError as exc:
            raise BusinessException(
                f"forward to memory API failed: {exc}",
                code=BizCode.SERVICE_UNAVAILABLE,
            ) from exc
        except MemoryProfileProtocolError as exc:
            raise BusinessException(
                str(exc), code=BizCode.SERVICE_UNAVAILABLE
            ) from exc
        if envelope.get("code") != 0:
            detail = envelope.get("msg") or envelope.get("error")
            raise BusinessException(
                str(detail or "memory API rejected the request"),
                code=as_biz_code(envelope.get("code")),
            )
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        try:
            return uuid.UUID(str(data["config_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessException(
                "memory API returned no active config id",
                code=BizCode.SERVICE_UNAVAILABLE,
            ) from exc
