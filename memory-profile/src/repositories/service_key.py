"""Read-only service key binding lookup."""

from uuid import UUID

from sqlalchemy import or_, select

from src.common.datetime_utils import utcnow_naive
from src.infrastructure.postgres import PostgresManager
from src.models.references.dashboard_model import DashboardApiKey
from src.models.references.workspace_model import Workspace


class ServiceKeyRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def is_bound_service_key(
        self, *, api_key_id: UUID, workspace_id: UUID, tenant_id: UUID
    ) -> bool:
        """Return whether a live service key is bound to this workspace in this tenant."""
        statement = (
            select(DashboardApiKey.id)
            .join(Workspace, Workspace.id == DashboardApiKey.workspace_id)
            .where(
                DashboardApiKey.id == api_key_id,
                DashboardApiKey.type == "service",
                DashboardApiKey.resource_id == workspace_id,
                DashboardApiKey.workspace_id == workspace_id,
                DashboardApiKey.is_active.is_(True),
                or_(
                    DashboardApiKey.expires_at.is_(None),
                    DashboardApiKey.expires_at > utcnow_naive(),
                ),
                Workspace.tenant_id == tenant_id,
                Workspace.is_active.is_(True),
            )
            .execution_options(autoflush=False)
        )
        async with self.postgres.session() as session:
            return (await session.scalar(statement)) is not None
