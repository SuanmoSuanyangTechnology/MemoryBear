"""Resolve configuration ids and enforce workspace ownership.

Only UUIDs and UUID strings are accepted: the legacy integer ``config_id_old``
spelling is gone, so a caller that has not migrated can be told plainly that the
id is invalid instead of being silently mapped onto another configuration.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from src.repositories.memory_config import MemoryConfigReadRepository
from src.services.memory_config.errors import (
    ConfigAccessDeniedError,
    ConfigNotFoundError,
    InvalidConfigIdError,
)


async def resolve_config_id(
    repository: MemoryConfigReadRepository,
    config_id: UUID | str,
) -> UUID:
    """Resolve a UUID or UUID string, or raise ``InvalidConfigIdError``."""

    if isinstance(config_id, UUID):
        return config_id
    if isinstance(config_id, str):
        try:
            return UUID(config_id.strip())
        except ValueError as exc:
            raise InvalidConfigIdError(
                f"无效的 config_id 格式: '{config_id}'（必须是 UUID）"
            ) from exc
    raise InvalidConfigIdError(f"不支持的 config_id 类型: {type(config_id).__name__}")


async def require_owned_config_exists(
    repository: MemoryConfigReadRepository,
    config_id: UUID | str,
    workspace_id: UUID,
) -> None:
    """Ownership check for writes that never read the row's fields.

    Fetches only ``config_id`` instead of the whole row; the access-denied vs
    not-found distinction is kept on the failure path.
    """

    resolved = await resolve_config_id(repository, config_id)
    if await repository.exists_owned(resolved, workspace_id):
        return
    if await repository.exists(resolved):
        raise ConfigAccessDeniedError(str(resolved))
    raise ConfigNotFoundError(str(resolved))


async def require_owned_config(
    repository: MemoryConfigReadRepository,
    config_id: UUID | str,
    workspace_id: UUID,
    *,
    columns: Iterable[Any] | None = None,
    workspace_columns: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Return the configuration row or raise a legacy ownership failure.

    A row in another workspace is reported as access-denied rather than
    not-found so the handler can keep the monolith's two distinct messages;
    both map to ``code=9503``. ``columns`` restricts the SELECT to the fields
    the caller actually reads; ``workspace_columns`` joins the workspace row in
    the same statement so callers that need both pay one round trip.
    """

    resolved = await resolve_config_id(repository, config_id)
    row = await repository.get_owned(
        resolved, workspace_id, columns=columns, workspace_columns=workspace_columns
    )
    if row is not None:
        return row
    if await repository.exists(resolved):
        raise ConfigAccessDeniedError(str(resolved))
    raise ConfigNotFoundError(str(resolved))
