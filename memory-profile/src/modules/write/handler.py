"""Transport adapter for the ``/memory`` endpoints this service still serves.

``POST /memory/write`` was removed with the write pipeline: memory-jobs owns the
write path, so this service no longer fronts it. ``POST /memory/emotion-reply``
was removed too: the API keeps its own local implementation. Only merge (run
in-process; it publishes the reflection task to the API worker) and the
placeholder info route remain. This module keeps the host's outer envelope: a
success ``ApiResponse(data, msg)`` or the legacy ``fail(code, msg, error)`` body.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi.responses import JSONResponse

from src.common.errors import BizCode
from src.common.responses import (
    legacy_fail_response,
    success,
)
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.write import WriteApiResponse

logger = logging.getLogger(__name__)

MERGE_FAILED_MSG = "合并失败"


class MemoryHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime
        # Ports are pure assembly (no I/O); merge resolves the end user and the
        # workspace's active config before it acquires any lock.
        from src.core.adapters.read.config import build_config_port, build_end_user_port

        self._end_users = build_end_user_port(runtime)
        self._configs = build_config_port(runtime)

    async def merge(
        self,
        context: MemoryPrincipal,
        payload: dict,
    ) -> WriteApiResponse | JSONResponse:
        """Run the merge in-process; reflection is published to the API worker."""

        from src.infrastructure.forget_candidates import ForgetCandidateRefresher
        from src.infrastructure.graph.end_user_merge import build_graph_merge
        from src.infrastructure.graph.memory_count import build_graph_memory_count
        from src.infrastructure.metadata_migration_llm import LlmMetadataMigrator
        from src.infrastructure.rag.end_user_reassign import (
            build_rag_memory_count,
            build_rag_merge,
        )
        from src.infrastructure.scene_names import build_scene_name_resolver
        from src.services.end_user_merge import (
            EndUserMergeService,
            MergeNotReady,
            MergeRejected,
            missing_end_users,
        )

        try:
            source = {
                uuid.UUID(str(value)) for value in payload.get("end_user_ids") or []
            }
            target = uuid.UUID(str(payload.get("target")))
        except (TypeError, ValueError):
            return legacy_fail_response(
                BizCode.INVALID_PARAMETER, "合并参数无效", "invalid merge payload"
            )

        # API controller parity: a target named among the sources is dropped
        # rather than rejected, and the workspace-wide existence check runs
        # before the merge service acquires any lock.
        source.discard(target)
        if not source:
            return legacy_fail_response(
                BizCode.USER_NOT_FOUND, "No users found.", "no source users"
            )
        missing = await missing_end_users(
            self.runtime.postgres, context.workspace_id, source | {target}
        )
        if missing:
            return legacy_fail_response(
                BizCode.USER_NOT_FOUND,
                f"Not found users - {missing}.",
                "not found users",
            )

        service = EndUserMergeService(
            postgres=self.runtime.postgres,
            settings=self.runtime.settings,
            # The merge only publishes the API's reflection task, so it must use
            # the API-facing producer: the memory-jobs bridge declares queues
            # with ``x-max-priority``, which ``reflection_tasks`` (API-owned,
            # declared without it) would reject with PRECONDITION_FAILED.
            task_bridge=self.runtime.api_tasks,
            graph_merge=build_graph_merge(self.runtime.postgres, self.runtime.neo4j),
            rag_merge=build_rag_merge(self.runtime.elasticsearch),
            graph_memory_count=build_graph_memory_count(self.runtime.neo4j),
            rag_memory_count=build_rag_memory_count(self.runtime.elasticsearch),
            config_resolver=self._resolve_reflection_config,
            migrate_metadata=LlmMetadataMigrator(
                postgres=self.runtime.postgres,
                model_service=self.runtime.model_service,
                settings=self.runtime.settings,
            ),
            scene_names=build_scene_name_resolver(self.runtime.postgres),
            refresh_forget_candidates=ForgetCandidateRefresher(
                postgres=self.runtime.postgres,
                neo4j=self.runtime.neo4j,
                redis_manager=self.runtime.redis,
                limits=self.runtime.quota.effective_limits,
            ),
        )
        try:
            outcome = await service.merge(source, target)
        except MergeRejected as exc:
            code = (
                BizCode.USER_NOT_FOUND
                if exc.reason == "user_not_found"
                else BizCode.INVALID_PARAMETER
            )
            return legacy_fail_response(code, exc.message, exc.message)
        except MergeNotReady as exc:
            return legacy_fail_response(
                BizCode.INTERNAL_ERROR, MERGE_FAILED_MSG, str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - boundary maps to the host envelope
            logger.exception("end-user merge failed")
            return legacy_fail_response(
                BizCode.INTERNAL_ERROR, MERGE_FAILED_MSG, str(exc)
            )
        return success(data={"end_user_id": outcome.end_user_id})

    async def _resolve_reflection_config(self, end_user_id: uuid.UUID):
        """Resolve the target's workspace active config for the reflection task."""

        target = await self._end_users.write_target(str(end_user_id))
        if target is None:
            return None
        return await self._configs.active_config_id(target["workspace_id"])

    def memory_info(self) -> Any:
        """The host's ``GET /memory`` placeholder payload."""

        return success(data={}, msg="Memory API - Coming Soon")