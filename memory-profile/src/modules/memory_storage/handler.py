"""HTTP boundary for memory-storage operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse, failure, success
from src.middleware.auth import MemoryPrincipal

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime
from src.services.memory_storage import MemoryStorageService


class MemoryStorageHandler:
    def __init__(self, runtime: ProfileRuntime):
        self.service = MemoryStorageService(runtime)

    async def timeline_memories(self, context: MemoryPrincipal, id: str, label: str) -> ApiResponse:
        data = await self.service.timeline_memories(
            id=id, label=label, workspace_id=context.workspace_id
        )
        return success(data, msg="共同记忆时间线")

    async def entity_timeline(self, context: MemoryPrincipal, **filters) -> ApiResponse:
        data = await self.service.entity_timeline(workspace_id=context.workspace_id, **filters)
        return success(data, msg="记忆时间线")

    async def relationship_evolution(
        self, context: MemoryPrincipal, id: str, label: str
    ) -> ApiResponse:
        data = await self.service.relationship_evolution(
            id=id, label=label, workspace_id=context.workspace_id
        )
        return success(data, msg="关系演变")

    def pilot_run(self, context, payload, language):
        return self.service.pilot_run_stream(
            payload,
            workspace_id=context.workspace_id,
            tenant_id=context.tenant_id,
            language=language,
        )

    def chat(self, context, payload, language):
        return self.service.trial_run_chat_stream(
            payload,
            workspace_id=context.workspace_id,
            tenant_id=context.tenant_id,
            language=language,
        )

    async def delete_end_user(self, context, end_user_id):
        try:
            data = await self.service.delete_end_user(
                end_user_id, workspace_id=context.workspace_id
            )
            return success(data, msg=f"删除用户{end_user_id}记忆库成功")
        except ProfileError:
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("End-user memory deletion failed")
            return failure(BizCode.INTERNAL_ERROR, "删除终端用户失败", str(exc))

    async def delete_node(self, context, payload):
        try:
            data = await self.service.delete_node(
                payload,
                workspace_id=context.workspace_id,
                tenant_id=context.tenant_id,
                operator=context.actor_id,
            )
            return success(data, msg="节点删除成功")
        except ProfileError:
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("Memory node deletion failed")
            return failure(BizCode.INTERNAL_ERROR, "节点删除失败", str(exc))
