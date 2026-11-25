"""Publish named jobs and await the existing synchronous result contract."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError
from task_bridge import BridgeError, TaskTimeoutError
from task_bridge.mem_router import TaskName

from src.common.errors import BizCode, ProfileError
from src.common.normalization import parse_uuid
from src.middleware.auth import MemoryPrincipal
from src.repositories.operations import OperationRepository
from src.runtime import ProfileRuntime
from src.schemas.operations import (
    EmotionSuggestionsResult,
    ForgettingReport,
    RAGProfileResult,
)
from src.services.end_user import EndUserService

logger = logging.getLogger(__name__)

_NAMES = {
    "rag_profile": TaskName.RAG_PROFILE_GENERATE,
    "emotion_suggestions": TaskName.EMOTION_SUGGESTIONS_GENERATE,
    "forgetting": TaskName.FORGETTING_TRIGGER,
}
# Worker hard limit is 900s, soft 840s. The caller waits for the actual report,
# never returns a fabricated 'accepted' response as if execution had completed.
WAIT_SECONDS = 920.0


class OperationService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repo = OperationRepository(runtime.postgres)
        self.users = EndUserService.from_postgres(runtime.postgres)
        self.bridge = runtime.task_bridge

    async def execute(
        self,
        action: str,
        context: MemoryPrincipal,
        language: str,
        *,
        end_user_id: str | None = None,
        config_id: str | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        if action not in _NAMES:
            raise ProfileError(BizCode.INVALID_PARAMETER, "未知任务类型")
        if not end_user_id:
            raise ProfileError(BizCode.INVALID_PARAMETER, "end_user_id 不能为空")
        if end_user_id is not None:
            user = parse_uuid(end_user_id)
            if user is None:
                raise ProfileError(BizCode.INVALID_PARAMETER, "无效终端用户 ID")
            if not await self.users.exists_in_workspace(user, context.workspace_id):
                raise ProfileError(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问")
            end_user_id = str(user)
        row = await self.repo.configuration(context.workspace_id, context.tenant_id, config_id)
        if row is None:
            raise ProfileError(BizCode.INVALID_PARAMETER, "记忆配置不存在或不属于当前工作空间")
        storage = row["storage_type"] or "neo4j"
        if (action == "rag_profile" and storage != "rag") or (
            action != "rag_profile" and storage != "neo4j"
        ):
            raise ProfileError(BizCode.INVALID_PARAMETER, "工作空间存储类型不支持该任务")
        payload = {
            **parameters,
            "workspace_id": str(context.workspace_id),
            "tenant_id": str(context.tenant_id),
            "actor_id": str(context.actor_id),
            "language": language,
            "config_id": str(row["config_id"]),
        }
        if end_user_id is not None:
            payload["end_user_id"] = end_user_id
        try:
            task_id = await self.bridge.submit(_NAMES[action], kwargs=payload)
            status = await self.bridge.await_result(task_id, wait_seconds=WAIT_SECONDS)
        except TaskTimeoutError as exc:
            raise ProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "任务仍在执行，等待结果超时；请勿立即重复触发",
                status_code=200,
            ) from exc
        except BridgeError as exc:
            logger.exception("operation bridge failed action=%s", action)
            raise ProfileError(
                BizCode.SERVICE_UNAVAILABLE, "任务服务暂不可用", status_code=200
            ) from exc
        if not status.successful:
            logger.error(
                "operation failed action=%s task=%s state=%s", action, task_id, status.state
            )
            raise ProfileError(
                BizCode.INTERNAL_ERROR, "任务执行失败，请查看任务日志", status_code=200
            )
        result = status.result
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ProfileError(BizCode.INTERNAL_ERROR, "任务结果格式无效", status_code=200)
        schema = {
            "rag_profile": RAGProfileResult,
            "emotion_suggestions": EmotionSuggestionsResult,
            "forgetting": ForgettingReport,
        }[action]
        try:
            return schema.model_validate(result["data"]).model_dump(mode="json")
        except ValidationError as exc:
            raise ProfileError(BizCode.INTERNAL_ERROR, "任务结果格式无效", status_code=200) from exc
