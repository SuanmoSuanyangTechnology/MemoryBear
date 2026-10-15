"""Fail-closed stand-in for the workspace memory-config resolver.

The active memory configuration id is owned by the monolith memory module and
is not readable from this service yet. Callers that need it during trial-run
fail with a clear error instead of running without a resolved memory config.
"""

import uuid

from ..errors import BizCode, BusinessException


class MemoryConfigService:
    """Monolith-compatible facade whose queries are fail-closed here."""

    def __init__(self, db=None) -> None:
        self.db = db

    async def get_workspace_active_config_id_async(
            self,
            workspace_id: uuid.UUID,
    ) -> uuid.UUID:
        raise BusinessException(
            "The active memory configuration is served by the memory API and is "
            "not readable from this service yet",
            code=BizCode.INVALID_PARAMETER,
        )
