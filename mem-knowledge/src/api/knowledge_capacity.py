"""社区版工作空间容量检查。"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from ..errors import KnowledgeError
from ..models.owned.document import Document
from ..models.owned.knowledge import Knowledge
from ..models.references.workspace import Workspace
from ..runtime import ProcessRuntime
from .dependencies import Principal, is_manager_request

# 兼容老单体 API 的 GB 口径。
_BYTES_PER_GB = 1024 ** 3


class WorkspaceCapacityExceeded(Exception):
    def __init__(self, used_gb: Decimal, limit_gb: Decimal) -> None:
        super().__init__("QUOTA_EXCEEDED")
        self.used_gb = used_gb
        self.limit_gb = limit_gb


async def check_workspace_capacity(
    request: Request,
    runtime: ProcessRuntime,
    principal: Principal,
) -> None:
    settings = runtime.settings
    limit_gb = settings.knowledge_capacity_quota_gb
    if (
        limit_gb is None
        or settings.deployment_mode.strip().lower() != "community"
        or settings.kb_auth_mode != "direct"
        or not is_manager_request(request)
    ):
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
            used_bytes = await db.scalar(stmt)
    except OperationalError as exc:
        raise KnowledgeError.from_code("KB_DATABASE_UNAVAILABLE") from exc

    used_gb = Decimal(used_bytes or 0) / _BYTES_PER_GB
    if used_gb >= limit_gb:
        raise WorkspaceCapacityExceeded(used_gb, limit_gb)
