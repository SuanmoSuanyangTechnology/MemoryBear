"""Return stable details locally; otherwise await the actual jobs result."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from task_bridge import BridgeError, TaskTimeoutError
from task_bridge.mem_router import TaskName

from src.common.errors import BizCode, ProfileError
from src.middleware.auth import MemoryPrincipal
from src.repositories.conversation_detail import ConversationDetailRepository
from src.schemas.working_memory import ConversationDetailData
from src.services.operations import WAIT_SECONDS

logger = logging.getLogger(__name__)
_ERROR_CODES = {
    "invalid_conversation": BizCode.INVALID_CONVERSATION,
    "model_not_found": BizCode.MODEL_NOT_FOUND,
    "llm_error": BizCode.LLM_ERROR,
    "source_changed": BizCode.SERVICE_UNAVAILABLE,
}


class ConversationDetailService:
    def __init__(self, runtime) -> None:
        self.repo = ConversationDetailRepository(runtime.postgres)
        self.bridge = runtime.task_bridge

    async def detail(
        self, context: MemoryPrincipal, end_user_id: str, conversation_id: str
    ) -> ConversationDetailData:
        try:
            uid, cid = UUID(end_user_id), UUID(conversation_id)
        except ValueError as exc:
            raise ProfileError(BizCode.INVALID_PARAMETER, "无效用户或会话 ID") from exc
        row = await self.repo.read(context.workspace_id, context.tenant_id, uid, cid)
        if row is None:
            raise ProfileError(BizCode.INVALID_CONVERSATION, "Conversation not found")
        updated = row["updated_at"]
        if updated is not None:
            updated = updated.replace(tzinfo=UTC) if updated.tzinfo is None else updated
        if row["detail_id"] and updated and datetime.now(UTC) - updated > timedelta(days=1):
            return ConversationDetailData.model_validate({
                **row, "question": row["question"] or [], "summary": row["summary"] or "",
                "theme": row["theme"] or "", "takeaways": row["takeaways"] or [],
                "info_score": row["info_score"] or 0,
            })
        payload = {
            "workspace_id": str(context.workspace_id),
            "tenant_id": str(context.tenant_id),
            "actor_id": str(context.actor_id),
            "end_user_id": str(uid),
            "conversation_id": str(cid),
            # The existing controller never passes request language to the service.
            "language": "zh",
        }
        try:
            task_id = await self.bridge.submit(
                TaskName.WORKING_MEMORY_DETAIL_GENERATE, kwargs=payload
            )
            status = await self.bridge.await_result(task_id, wait_seconds=WAIT_SECONDS)
        except TaskTimeoutError as exc:
            raise ProfileError(
                BizCode.SERVICE_UNAVAILABLE, "任务仍在执行，等待结果超时；请勿立即重复触发"
            ) from exc
        except BridgeError as exc:
            logger.exception("conversation detail bridge failed")
            raise ProfileError(BizCode.SERVICE_UNAVAILABLE, "任务服务暂不可用") from exc
        if not status.successful:
            raise ProfileError(BizCode.INTERNAL_ERROR, "任务执行失败，请查看任务日志")
        result = status.result
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ProfileError(BizCode.INTERNAL_ERROR, "任务结果格式无效")
        data = result["data"]
        if isinstance(data.get("error"), dict):
            error = data["error"]
            raise ProfileError(
                _ERROR_CODES.get(error.get("kind"), BizCode.INTERNAL_ERROR),
                str(error.get("message") or "任务执行失败"),
            )
        try:
            return ConversationDetailData.model_validate(data)
        except ValidationError as exc:
            raise ProfileError(BizCode.INTERNAL_ERROR, "任务结果格式无效") from exc
