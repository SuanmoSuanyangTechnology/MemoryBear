import uuid
import json

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.dependencies import get_current_user, get_db
from app.models.prompt_optimizer_model import RoleType
from app.schemas.prompt_optimizer_schema import PromptOptMessage, PromptOptModelSet, CreateSessionResponse, \
    OptimizePromptResponse, SessionHistoryResponse, SessionMessage
from app.schemas.response_schema import ApiResponse
from app.services.prompt_optimizer_service import PromptOptimizerService

router = APIRouter(prefix="/prompt", tags=["Prompts-Optimization"])
logger = get_api_logger()


@router.post(
    "/sessions",
    summary="Create a new prompt optimization session",
    response_model=ApiResponse
)
def create_prompt_session(
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """
    Create a new prompt optimization session for the current user.

    Returns:
        ApiResponse: Contains the newly generated session ID.
    """
    service = PromptOptimizerService(db)
    # create new session
    session = service.create_session(current_user.tenant_id, current_user.id)
    result_schema = CreateSessionResponse.model_validate(session)
    return success(data=result_schema)


@router.get(
    "/sessions/{session_id}",
    summary="获取 prompt 优化历史对话",
    response_model=ApiResponse
)
def get_prompt_session(
        session_id: uuid.UUID = Path(..., description="Session ID"),
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """
    Retrieve all messages from a specified prompt optimization session.

    Args:
        session_id (UUID): The ID of the session to retrieve
        db (Session): Database session
        current_user: Current logged-in user

    Returns:
        ApiResponse: Contains the session ID and the list of messages.
    """
    service = PromptOptimizerService(db)

    history = service.get_session_message_history(
        session_id=session_id,
        user_id=current_user.id
    )

    messages = [
        SessionMessage(role=role, content=content)
        for role, content in history
    ]

    result = SessionHistoryResponse(
        session_id=session_id,
        messages=messages
    )

    return success(data=result)


@router.post(
    "/sessions/{session_id}/messages",
    summary="Get prompt optimization",
    response_model=ApiResponse
)
async def get_prompt_opt(
        session_id: uuid.UUID = Path(..., description="Session ID"),
        data: PromptOptMessage = ...,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """
    Send a user message in the specified session and return the optimized prompt
    along with its description and variables.

    Args:
        session_id (UUID): The session ID
        data (PromptOptMessage): Contains the user message, model ID, and current prompt
        db (Session): Database session
        current_user: Current user information

    Returns:
        ApiResponse: Contains the optimized prompt, description, and a list of variables.
    """
    service = PromptOptimizerService(db)

    async def event_generator():
        yield "event:start\ndata: {}\n\n"
        try:
            async for chunk in service.optimize_prompt(
                    tenant_id=current_user.tenant_id,
                    model_id=data.model_id,
                    session_id=session_id,
                    user_id=current_user.id,
                    current_prompt=data.current_prompt,
                    user_require=data.message
            ):
                # chunk 是 prompt 的增量内容
                yield f"event:message\ndata: {json.dumps(chunk)}\n\n"
        except Exception as e:
            yield f"event:error\ndata: {json.dumps(
                {"error": str(e)}
            )}\n\n"
        yield "event:end\ndata: {}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
