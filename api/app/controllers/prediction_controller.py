import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import date

from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.prediction import PredictionPipeline, ProfileNotFound
from app.core.memory.prediction.config import load_prediction_settings
from app.core.memory.prediction.llm import StructuredLLM
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.dependencies import cur_workspace_access_guard, get_current_user
from app.models.user_model import User
from app.repositories.end_user_repository import get_end_user_by_id
from app.repositories.workspace_repository import WorkspaceRepository
from app.utils.sse_utils import format_sse_message
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory/predictions", tags=["Memory Prediction"])


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    end_user_id: uuid.UUID
    question: str = Field(min_length=1, max_length=2000)
    prediction_deadline: date = Field(
        validation_alias=AliasChoices("prediction_deadline", "deadline")
    )
    max_rounds: int = Field(default=4, ge=3, le=6)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question 不能为空")
        return value

    @field_validator("prediction_deadline")
    @classmethod
    def validate_deadline(cls, value: date) -> date:
        if value <= date.today():
            raise ValueError("prediction_deadline 必须晚于今天")
        return value


def _build_llm(db: Session, current_user: User) -> OpenAIClient:
    workspace = WorkspaceRepository(db).get_workspace_by_id(current_user.current_workspace_id)
    if workspace is None or not workspace.llm:
        raise HTTPException(status_code=409, detail="当前工作空间尚未配置 LLM")
    return MemoryClientFactory(db, tenant_id=current_user.tenant_id).get_llm_client(str(workspace.llm))


@router.post("/stream")
@cur_workspace_access_guard()
async def stream_prediction(
    payload: PredictionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    end_user = get_end_user_by_id(db, payload.end_user_id)
    if end_user is None:
        raise HTTPException(status_code=404, detail="end_user 不存在")
    if end_user.workspace_id != current_user.current_workspace_id:
        raise HTTPException(status_code=403, detail="无权访问该 end_user")
    resolved_end_user_id = str(end_user.id)
    client = _build_llm(db, current_user)
    pipeline = PredictionPipeline(StructuredLLM(client), load_prediction_settings())

    async def event_source() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict[str, object]] | None] = asyncio.Queue()

        async def emit(name: str, data: dict[str, object]) -> None:
            await queue.put((name, data))

        async def execute() -> None:
            try:
                result = await pipeline.run(
                    resolved_end_user_id,
                    payload.question,
                    payload.prediction_deadline.isoformat(),
                    max_rounds=payload.max_rounds,
                    on_event=emit,
                )
                await emit("done", result.export())
            except ProfileNotFound as exc:
                await emit("error", {"message": str(exc), "code": "PROFILE_NOT_FOUND"})
            except Exception as exc:
                logger.exception("Prediction run failed for end_user_id=%s", payload.end_user_id)
                await emit("error", {"message": str(exc), "code": type(exc).__name__})
            finally:
                await queue.put(None)

        task = asyncio.create_task(execute())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    yield format_sse_message("end", {})
                    break
                yield format_sse_message(item[0], json.loads(json.dumps(item[1], default=str)))
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_source(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
