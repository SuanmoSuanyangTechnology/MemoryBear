"""Generation use-cases: interest distribution and cache generation via memory-jobs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from task_bridge import (
    BridgeConfigurationError,
    BridgeError,
    InvalidTaskPayloadError,
    TaskPublishError,
    TaskResultUnavailableError,
    TaskTimeoutError,
)
from task_bridge.mem_router import TaskName

from src.common.errors import BizCode, LocalizedProfileError
from src.common.redis_cache import CACHE_MISS, get_json, set_json
from src.repositories.generation import (
    GenerationGraphRepository,
    MemoryGenerationRepository,
)
from src.runtime import ProfileRuntime
from src.services.end_user import EndUserService
from src.services.model_config import ModelConfigService
from src.services.profile import INTEREST_DISPLAY_LIMIT
from src.services.workspace import (
    REASON_UNSUPPORTED_STORAGE_TYPE,
    WorkspaceService,
    validate_workspace_row,
)

logger = logging.getLogger(__name__)

#: 与 api InterestMemoryCache.PREFIX 同 key，两侧实现可互读缓存。
INTEREST_CACHE_PREFIX = "cache:memory:interest_distribution"

#: 各生成用例等待 memory-jobs 终态的预算（秒），在投递处与提交动作成对出现。
_INTEREST_WAIT_TIMEOUT_SECONDS = 120.0
_GENERATE_CACHE_WAIT_TIMEOUT_SECONDS = 960.0
_GENERATE_CACHE_WORKSPACE_WAIT_TIMEOUT_SECONDS = 3600.0


class GenerationService:
    """兴趣分布与洞察/摘要缓存生成的编排（生成在 memory-jobs 执行）。"""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.end_user = EndUserService.from_postgres(runtime.postgres)
        self.memory_generation = MemoryGenerationRepository(runtime.postgres)
        self.workspace = WorkspaceService.from_postgres(runtime.postgres)
        self.generation_graph = GenerationGraphRepository(runtime.neo4j)
        self.model_config = ModelConfigService.from_postgres(
            runtime.postgres, runtime.model_service
        )
        self.task_bridge = runtime.task_bridge
        self.interest_cache_expire = runtime.settings.interest_cache_expire_seconds

    async def interest_distribution(
        self, end_user_id: str, workspace_id: UUID, limit: int, language: str
    ) -> list[dict[str, Any]]:
        """读缓存或生成兴趣分布。"""

        if not await self.end_user.exists_in_workspace(end_user_id, workspace_id):
            return []

        cache_key = f"{INTEREST_CACHE_PREFIX}:by_user:{end_user_id}:{language}"
        cached = await self._interest_cache_get(cache_key)
        if cached is not None:
            return cached[:limit]

        generation = await self._generate_interest_via_worker(
            end_user_id, workspace_id, language
        )
        items = generation["items"]
        if generation["cacheable"]:
            await self._interest_cache_set(cache_key, items)
        return items[:limit]

    async def _generate_interest_via_worker(
        self, end_user_id: str, workspace_id: UUID, language: str
    ) -> dict[str, Any]:
        """Generate one interest distribution in memory-jobs; cache stays here."""

        try:
            task_id = await self.task_bridge.submit(
                TaskName.INTEREST_DISTRIBUTION_GENERATE,
                kwargs={
                    "end_user_id": end_user_id,
                    "workspace_id": str(workspace_id),
                    "language": language,
                    "limit": INTEREST_DISPLAY_LIMIT,
                },
            )
        except (BridgeConfigurationError, InvalidTaskPayloadError, TaskPublishError) as exc:
            logger.error(
                "interest distribution submit failed user=%s workspace=%s error=%s",
                end_user_id,
                workspace_id,
                type(exc).__name__,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.interest_unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                status_code=200,
            ) from exc

        try:
            status = await self.task_bridge.await_result(
                task_id, wait_seconds=_INTEREST_WAIT_TIMEOUT_SECONDS
            )
        except TaskTimeoutError as exc:
            logger.warning(
                "interest distribution still running after wait timeout user=%s task=%s",
                end_user_id,
                task_id,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.interest_timeout",
                detail=str(exc),
                status_code=200,
            ) from exc
        except (TaskResultUnavailableError, BridgeError) as exc:
            logger.error(
                "interest distribution result unavailable user=%s task=%s error=%s",
                end_user_id,
                task_id,
                type(exc).__name__,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.interest_unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                status_code=200,
            ) from exc

        payload = status.result if isinstance(status.result, dict) else None
        if status.successful and payload is not None:
            raw_items = payload.get("items")
            items = (
                [item for item in raw_items if isinstance(item, dict)]
                if isinstance(raw_items, list)
                else []
            )
            return {
                "items": items,
                "cacheable": bool(payload.get("cacheable", True)),
            }

        logger.error(
            "interest distribution task failed user=%s task=%s state=%s error=%s",
            end_user_id,
            task_id,
            status.state,
            status.error,
        )
        return {"items": [], "cacheable": True}

    async def _interest_cache_get(self, key: str) -> list[dict[str, Any]] | None:
        try:
            payload = await get_json(key)
        except Exception as exc:
            logger.warning(
                "interest cache read unavailable: key=%s error=%s",
                key,
                type(exc).__name__,
            )
            return None
        if payload is CACHE_MISS or not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        return [item for item in data if isinstance(item, dict)]

    async def _interest_cache_set(self, key: str, items: list[dict[str, Any]]) -> None:
        payload = {
            "data": items,
            "generated_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "cached": True,
        }
        try:
            await set_json(key, payload, self.interest_cache_expire)
        except Exception as exc:
            logger.warning(
                "interest cache write unavailable: key=%s error=%s",
                key,
                type(exc).__name__,
            )

    async def generate_cache(
        self,
        workspace_id: UUID,
        end_user_id: str | None,
        language: str,
    ) -> dict[str, Any]:
        """为单个终端用户或整个工作空间生成洞察与摘要缓存。"""

        workspace_row = await self.workspace.row(workspace_id)
        eligibility = validate_workspace_row(workspace_row)
        if not eligibility.valid:
            if eligibility.reason == REASON_UNSUPPORTED_STORAGE_TYPE:
                raise LocalizedProfileError(
                    BizCode.INVALID_PARAMETER,
                    "analytics.generate.unsupported_storage_type",
                    detail=f"unsupported storage_type: {eligibility.storage_type or 'unknown'}",
                )
            raise LocalizedProfileError(
                BizCode.INVALID_PARAMETER,
                "analytics.generate.workspace_unavailable",
                detail=eligibility.reason,
            )

        if end_user_id:
            return await self._generate_cache_via_worker(
                end_user_id, workspace_id, language
            )
        return await self._generate_cache_batch_via_worker(workspace_id, language)

    async def _generate_cache_via_worker(
        self,
        end_user_id: str,
        workspace_id: UUID,
        language: str,
    ) -> dict[str, Any]:
        """Submit one generation unit to memory-jobs and wait for its outcome."""

        try:
            task_id = await self.task_bridge.submit(
                TaskName.PROFILE_CACHE_GENERATE,
                kwargs={
                    "end_user_id": end_user_id,
                    "workspace_id": str(workspace_id),
                    "language": language,
                },
            )
        except (BridgeConfigurationError, InvalidTaskPayloadError, TaskPublishError) as exc:
            logger.error(
                "generate_cache submit failed user=%s workspace=%s error=%s",
                end_user_id,
                workspace_id,
                type(exc).__name__,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                status_code=200,
            ) from exc

        try:
            status = await self.task_bridge.await_result(
                task_id, wait_seconds=_GENERATE_CACHE_WAIT_TIMEOUT_SECONDS
            )
        except TaskTimeoutError as exc:
            logger.warning(
                "generate_cache still running after wait timeout user=%s task=%s",
                end_user_id,
                task_id,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.in_progress",
                detail=str(exc),
                status_code=200,
            ) from exc
        except (TaskResultUnavailableError, BridgeError) as exc:
            logger.error(
                "generate_cache result unavailable user=%s task=%s error=%s",
                end_user_id,
                task_id,
                type(exc).__name__,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                status_code=200,
            ) from exc

        payload = status.result if isinstance(status.result, dict) else None
        if status.successful and payload is not None:
            if payload.get("status") == "skipped":
                raise LocalizedProfileError(
                    BizCode.SERVICE_UNAVAILABLE,
                    "analytics.generate.in_progress",
                    detail="task skipped: another refresh holds the lock",
                    status_code=200,
                )
            return {
                "end_user_id": payload.get("end_user_id", end_user_id),
                "insight_success": payload.get("insight_success"),
                "summary_success": payload.get("summary_success"),
                "errors": payload.get("errors") or [],
            }

        error = status.error or "unknown error"
        logger.error(
            "generate_cache task failed user=%s task=%s state=%s error=%s",
            end_user_id,
            task_id,
            status.state,
            error,
        )
        return {
            "end_user_id": end_user_id,
            "insight_success": False,
            "summary_success": False,
            "errors": [{"type": "task", "error": error}],
        }

    async def _generate_cache_batch_via_worker(
        self, workspace_id: UUID, language: str
    ) -> dict[str, Any]:
        """Ask memory-jobs to fan the workspace out and wait for its summary."""

        try:
            task_id = await self.task_bridge.submit(
                TaskName.PROFILE_CACHE_GENERATE_WORKSPACE,
                kwargs={"workspace_id": str(workspace_id), "language": language},
            )
        except (BridgeConfigurationError, InvalidTaskPayloadError, TaskPublishError) as exc:
            logger.error(
                "generate_cache batch submit failed workspace=%s error=%s",
                workspace_id,
                type(exc).__name__,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                status_code=200,
            ) from exc

        try:
            status = await self.task_bridge.await_result(
                task_id, wait_seconds=_GENERATE_CACHE_WORKSPACE_WAIT_TIMEOUT_SECONDS
            )
        except TaskTimeoutError as exc:
            logger.warning(
                "generate_cache batch still running after wait timeout workspace=%s task=%s",
                workspace_id,
                task_id,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.in_progress",
                detail=str(exc),
                status_code=200,
            ) from exc
        except (TaskResultUnavailableError, BridgeError) as exc:
            logger.error(
                "generate_cache batch result unavailable workspace=%s task=%s error=%s",
                workspace_id,
                task_id,
                type(exc).__name__,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.generate.unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                status_code=200,
            ) from exc

        payload = status.result if isinstance(status.result, dict) else None
        if status.successful and payload is not None:
            return {
                "total_users": payload.get("total_users", 0),
                "successful": payload.get("successful", 0),
                "failed": payload.get("failed", 0),
                "errors": payload.get("errors") or [],
            }

        error = status.error or "unknown error"
        logger.error(
            "generate_cache batch task failed workspace=%s task=%s state=%s error=%s",
            workspace_id,
            task_id,
            status.state,
            error,
        )
        return {
            "total_users": 0,
            "successful": 0,
            "failed": 0,
            "errors": [{"error": f"task {task_id} failed: {error}"}],
        }
