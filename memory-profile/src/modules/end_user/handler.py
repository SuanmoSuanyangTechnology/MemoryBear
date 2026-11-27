"""Transport handlers for the two end-user callbacks.

Responsibilities only: resolve the caller's workspace, map the validated body
onto the use cases, and keep the host's envelope and business codes. All data
access lives in ``src.repositories`` / ``src.services``.
"""

from __future__ import annotations

import logging
from typing import Any

from src.common.errors import BizCode
from src.common.responses import ApiResponse, failure, success
from src.middleware.auth import MemoryPrincipal
from src.modules.end_user.schemas import DomainInfoBody, InfoUpdateBody
from src.repositories.end_user_info import EndUserInfoReadRepository
from src.repositories.end_user_info_mutation import EndUserInfoMutationRepository
from src.repositories.outbox import OutboxRepository
from src.runtime import ProfileRuntime
from src.services.end_user_domain import project_domains, resolve_scene_names
from src.services.end_user_info import (
    EndUserInfoInvalidIdError,
    EndUserInfoNotFoundError,
    EndUserInfoUpdateService,
)

logger = logging.getLogger(__name__)


class EndUserHandler:
    """Serve the application-service end-user callbacks for one request."""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime
        self._reads = EndUserInfoReadRepository(runtime.postgres)
        self._mutations = EndUserInfoMutationRepository(runtime.postgres)

    # ── domain info ──────────────────────────────────────────────

    async def domain_info(
        self, context: MemoryPrincipal, body: DomainInfoBody
    ) -> ApiResponse[dict[str, Any]]:
        """Return the assembled ``domain_specific_info`` display projection."""

        resolved = await self._resolve(context, body.end_user_id)
        if resolved is None:
            return failure(
                BizCode.USER_NOT_FOUND,
                "终端用户不存在",
                "end_user not found",
            )
        target_id, workspace_id = resolved

        row = await self._reads.get_display_row(target_id)
        if row is None:
            return failure(
                BizCode.USER_NOT_FOUND,
                "终端用户信息记录不存在",
                "终端用户信息记录不存在",
            )

        meta = row["meta_data_new"]
        scene_names = await resolve_scene_names(
            meta,
            postgres=self.runtime.postgres,
            workspace_id=workspace_id,
            enterprise_lookup=self._enterprise_scene_names(workspace_id),
        )
        field_labels = await resolve_field_labels(
            meta, enterprise_lookup=self._enterprise_field_labels(workspace_id)
        )
        return success(
            data={
                "domain_specific_info": project_domains(
                    meta, scene_names, field_labels
                )
            },
            msg="查询成功",
        )

    # ── info update ──────────────────────────────────────────────

    async def info_update(
        self, context: MemoryPrincipal, body: InfoUpdateBody
    ) -> ApiResponse[dict[str, Any]]:
        """Apply the info update and its alias side effect."""

        service = EndUserInfoUpdateService(
            reads=self._reads,
            mutations=self._mutations,
            alias_writer=self._alias_writer(),
        )
        try:
            data = await service.update(
                workspace_id=context.workspace_id,
                end_user_id=body.end_user_id,
                update_data=body.model_dump(exclude_unset=True, exclude={"end_user_id"}),
            )
        except EndUserInfoInvalidIdError:
            return failure(
                BizCode.INVALID_PARAMETER,
                "无效的终端用户ID格式",
                "invalid uuid",
            )
        except EndUserInfoNotFoundError:
            return failure(
                BizCode.USER_NOT_FOUND,
                "终端用户信息记录不存在",
                "终端用户信息记录不存在",
            )
        return success(data=data, msg="更新成功")

    # ── wiring ───────────────────────────────────────────────────

    async def _resolve(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> tuple[Any, Any] | None:
        """``(merged target id, workspace id)`` or ``None`` outside this workspace."""

        import uuid

        try:
            parsed = uuid.UUID(str(end_user_id))
        except (ValueError, AttributeError, TypeError):
            return None
        resolved = await self._reads.get_active_or_merged_end_user(parsed)
        if resolved is None or str(resolved["workspace_id"]) != str(
            context.workspace_id
        ):
            return None
        return resolved["id"], context.workspace_id

    def _enterprise_scene_names(self, workspace_id: Any):
        """Scene-name lookup through memory-enterprise, or ``None`` when disabled."""

        enterprise = self.runtime.memory_enterprise
        if not getattr(enterprise, "enabled", False):
            return None
        from uuid import UUID

        async def _lookup(_workspace_id: UUID, scene_ids: list[str]) -> dict[str, str]:
            tenant_id = await self._tenant_id(workspace_id)
            if tenant_id is None:
                return {}
            return await enterprise.scene_names(
                tenant_id=tenant_id, scene_ids=scene_ids
            )

        return _lookup

    def _enterprise_field_labels(self, workspace_id: Any):
        """Field-label lookup through memory-enterprise, or ``None`` when disabled."""

        enterprise = self.runtime.memory_enterprise
        if not getattr(enterprise, "enabled", False):
            return None

        async def _lookup(scene_ids: list[str]) -> dict[str, dict[str, str]]:
            tenant_id = await self._tenant_id(workspace_id)
            if tenant_id is None:
                return {}
            return await enterprise.field_labels(
                tenant_id=tenant_id, scene_ids=scene_ids
            )

        return _lookup

    async def _tenant_id(self, workspace_id: Any):
        """The workspace's tenant, which the enterprise calls act for."""

        from src.repositories.workspace import WorkspaceRepository

        row = await WorkspaceRepository(self.runtime.postgres).workspace_row(
            workspace_id
        )
        return row.get("tenant_id") if row else None

    def _alias_writer(self):
        """Bind the graph alias write, or ``None`` without a graph writer."""

        from src.infrastructure.graph.client import Neo4jGraphWriter
        from src.repositories.end_user_aliases_mutation import (
            update_user_entity_aliases,
        )

        writer = Neo4jGraphWriter(self.runtime.neo4j)
        outbox = OutboxRepository(self.runtime.postgres)

        async def _write(end_user_id: str, aliases: list[str]):
            return await update_user_entity_aliases(
                end_user_id, aliases, client=writer, outbox_repository=outbox
            )

        return _write


__all__ = ["EndUserHandler"]
