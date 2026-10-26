"""Read-only workspace lookup shared by every module.

The row carries the workspace's identity, storage type and all six model
columns, so the generation entrypoint's validation and the model-config
resolution read it once instead of each querying its own subset.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.infrastructure.postgres import PostgresManager
from src.models.references import Workspace


class WorkspaceRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def workspace_row(self, workspace_id: UUID) -> dict[str, Any] | None:
        """工作空间所需列（活跃判定、存储类型、六类模型列与默认配置标记）。"""

        async with self.postgres.session() as session:
            row = (
                await session.execute(
                    select(
                        Workspace.id,
                        Workspace.name,
                        Workspace.tenant_id,
                        Workspace.is_active,
                        Workspace.storage_type,
                        Workspace.is_default_config,
                        Workspace.memory_config,
                        Workspace.llm,
                        Workspace.embedding,
                        Workspace.rerank,
                        Workspace.vision,
                        Workspace.audio,
                        Workspace.video,
                    )
                    .where(Workspace.id == workspace_id)
                    .limit(1)
                )
            ).mappings().one_or_none()
            return dict(row) if row is not None else None
