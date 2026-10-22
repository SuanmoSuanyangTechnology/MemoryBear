"""Workspace read slice for the workflow runtime.

Only the permission-free storage-type lookup the runtime and the debug
surfaces need; the full monolith workspace service (membership checks, model
config assembly) stays on the host side.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models.workspace_model import Workspace

business_logger = logging.getLogger(__name__)


def get_workspace_storage_type_without_auth(
        db: Session,
        workspace_id: uuid.UUID,
) -> str:
    """Storage type of a workspace without a permission check (public-share
    style lookups)."""

    business_logger.info(f"Fetching storage type of workspace {workspace_id} (no auth)")

    workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not workspace:
        business_logger.error(f"Workspace not found: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(f"Storage type of workspace {workspace_id}: {workspace.storage_type}")
    return workspace.storage_type


async def get_workspace_storage_type_without_auth_async(
        db: AsyncSession,
        workspace_id: uuid.UUID,
) -> str:
    """Async variant of :func:`get_workspace_storage_type_without_auth`."""

    business_logger.info(f"Fetching storage type of workspace {workspace_id} (no auth, async)")
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalars().first()
    if not workspace:
        business_logger.error(f"Workspace not found: workspace_id={workspace_id}")
        raise BusinessException(
            code=BizCode.WORKSPACE_NOT_FOUND,
            message="工作空间不存在"
        )

    business_logger.info(f"Storage type of workspace {workspace_id}: {workspace.storage_type}")
    return workspace.storage_type


__all__ = [
    "get_workspace_storage_type_without_auth",
    "get_workspace_storage_type_without_auth_async",
]
