"""Delegating facade for the workspace memory-config resolver.

The active memory configuration id is owned by the monolith memory module;
during the coexistence period this service reads it through the internal
``memory/workspace-config`` endpoint with an explicit workspace header. An
unconfigured channel fails closed, and upstream rejections (for example a
workspace without an enabled memory config) surface with the upstream error
message and code.
"""

import logging
import uuid

from ..errors import BizCode, BusinessException, as_biz_code
from ..infrastructure.upstream import UpstreamUnavailableError, get_monolith_upstream

logger = logging.getLogger(__name__)

_UNAVAILABLE_MESSAGE = (
    "The active memory configuration is served by the memory API and is "
    "not readable from this service yet"
)

_WORKSPACE_CONFIG_PATH = "/api/internal/app-service/memory/workspace-config"


class MemoryConfigService:
    """Monolith-compatible facade backed by the internal memory endpoint."""

    def __init__(self, db=None) -> None:
        self.db = db

    async def get_workspace_active_config_id_async(
            self,
            workspace_id: uuid.UUID,
    ) -> uuid.UUID:
        upstream = get_monolith_upstream()
        if upstream is None or not upstream.configured:
            raise BusinessException(_UNAVAILABLE_MESSAGE, code=BizCode.SERVICE_UNAVAILABLE)
        try:
            response = await upstream.forward_async(
                "GET",
                _WORKSPACE_CONFIG_PATH,
                headers={"X-Workspace-Id": str(workspace_id)},
            )
        except UpstreamUnavailableError as exc:
            raise BusinessException(
                f"forward to memory API failed: {exc}",
                code=BizCode.SERVICE_UNAVAILABLE,
            ) from exc
        body = response.body if isinstance(response.body, dict) else {}
        if response.status_code != 200 or body.get("code") != 0:
            raise BusinessException(
                str(body.get("msg") or body.get("error") or "memory API rejected the request"),
                code=as_biz_code(body.get("code")),
            )
        data = body.get("data") or {}
        return uuid.UUID(str(data["config_id"]))
