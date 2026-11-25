"""Build a :class:`ReadContext` from a caller identity and an end-user id.

Shared by the non-streaming and streaming use cases. It centralizes the merge
routing, the workspace-scoped configuration lookup and the storage-type
resolution so both faces cannot drift.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from src.core.domain.enums import StorageType
from src.core.domain.errors import (
    EndUserNotFoundError,
    ReadConfigUnavailableError,
)
from src.core.domain.models import ReadContext, ReadPrincipal
from src.core.domain.ports import EndUserPort, ReadConfigPort

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_TYPE = "neo4j"


class ReadContextFactory:
    """Assemble the retrieval context for one (principal, end user) pair."""

    def __init__(self, end_users: EndUserPort, configs: ReadConfigPort) -> None:
        self._end_users = end_users
        self._configs = configs

    async def build(
        self,
        *,
        principal: ReadPrincipal,
        end_user_id: str,
        language: str,
        draft: bool = False,
        conversation_id: str | None = None,
        app_id: UUID | None = None,
        config_id: UUID | None = None,
        storage_type: StorageType | None = None,
    ) -> ReadContext:
        resolved = await self._end_users.resolve_in_workspace(
            end_user_id, principal.workspace_id
        )
        if resolved is None:
            raise EndUserNotFoundError(
                "No users found.", reason="end_user_not_found"
            )
        canonical_end_user_id = resolved.end_user_id

        selected_config_id = config_id or await self._configs.active_config_id(
            principal.workspace_id
        )
        if selected_config_id is None:
            raise ReadConfigUnavailableError(
                "No memory configuration found for this workspace",
                reason="memory_config_missing",
            )
        config = await self._configs.load_config(selected_config_id)
        if config is None:
            raise ReadConfigUnavailableError(
                "Memory configuration not found", reason="memory_config_not_found"
            )
        if storage_type is None:
            storage_type = StorageType(
                await self._configs.workspace_storage_type(principal.workspace_id)
                or DEFAULT_STORAGE_TYPE
            )
        return ReadContext(
            end_user_id=canonical_end_user_id,
            workspace_id=principal.workspace_id,
            tenant_id=principal.tenant_id,
            config=config,
            storage_type=storage_type,
            language=language,
            draft=draft,
            conversation_id=conversation_id,
            app_id=app_id,
        )


#: Factory shape the use cases accept, so a test can substitute a fake builder.
ContextBuilder = Callable[..., Awaitable[ReadContext]]

__all__ = [
    "DEFAULT_STORAGE_TYPE",
    "ContextBuilder",
    "ReadContextFactory",
]
