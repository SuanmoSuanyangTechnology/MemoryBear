"""知识库写入前的工作空间容量检查。"""

import asyncio
import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..application.runtime import ProcessRuntime
from ..errors import KnowledgeError
from ..models.owned.document import Document
from ..models.owned.knowledge import Knowledge
from ..models.references.workspace import Workspace
from .dependencies import Principal, is_manager_request

# 兼容老单体 API 的 GB 口径。
_BYTES_PER_GB = 1024 ** 3
logger = logging.getLogger(__name__)


class WorkspaceCapacityExceeded(Exception):
    def __init__(self, used_gb: Decimal, limit_gb: Decimal) -> None:
        super().__init__("QUOTA_EXCEEDED")
        self.used_gb = used_gb
        self.limit_gb = limit_gb


async def _enterprise_capacity_limit(db: AsyncSession, tenant_id: UUID) -> Decimal | None:
    from enterprise_ext.knowledge_capacity import get_capacity_limit_gb

    return await get_capacity_limit_gb(db, tenant_id)


async def check_workspace_capacity(
    request: Request,
    runtime: ProcessRuntime,
    principal: Principal,
) -> None:
    if not is_manager_request(request):
        return
    settings = runtime.settings
    mode = settings.deployment_mode.strip().lower()
    limit_gb = settings.knowledge_capacity_quota_gb if mode == "community" else None
    if mode != "enterprise" and limit_gb is None:
        return

    stmt = (
        select(func.coalesce(func.sum(Document.file_size), 0))
        .join(Knowledge, Document.kb_id == Knowledge.id)
        .join(Workspace, Knowledge.workspace_id == Workspace.id)
        .where(
            Workspace.id == principal.workspace_id,
            Workspace.tenant_id == principal.tenant_id,
            Document.status == 1,
        )
    )
    try:
        async with runtime.database.async_session() as db:
            if mode == "enterprise":
                async with asyncio.timeout(2.0):
                    limit_gb = await _enterprise_capacity_limit(db, principal.tenant_id)
            if limit_gb is None:
                return
            used_bytes = await db.scalar(stmt)
    except (ImportError, SQLAlchemyError, TimeoutError, ArithmeticError, ValueError) as exc:
        logger.warning("知识库额度检查失败，模式=%s，类型=%s", mode, type(exc).__name__)
        raise KnowledgeError.from_code(
            "KB_DATABASE_UNAVAILABLE", status_code=503 if mode == "enterprise" else None,
        ) from exc

    used_gb = Decimal(used_bytes or 0) / _BYTES_PER_GB
    if used_gb >= limit_gb:
        raise WorkspaceCapacityExceeded(used_gb, limit_gb)
