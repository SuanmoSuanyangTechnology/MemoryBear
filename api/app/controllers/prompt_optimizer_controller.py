import json
import uuid

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.dependencies import get_current_user, get_db
from app.schemas.prompt_optimizer_schema import (
    PromptOptMessage,
    CreateSessionResponse,
    SessionHistoryResponse,
    SessionMessage,
    PromptSaveRequest
)
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


@router.post(
    "/releases",
    summary="Get prompt optimization",
    response_model=ApiResponse
)
def save_prompt(
        data: PromptSaveRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """
       Save a prompt release for the current tenant.

       Args:
           data (PromptSaveRequest): Request body containing session_id, title, and prompt.
           db (Session): SQLAlchemy database session, injected via dependency.
           current_user: Currently authenticated user object, injected via dependency.

       Returns:
           ApiResponse: Standard API response containing the saved prompt release info:
               - id: UUID of the prompt release
               - session_id: associated session
               - title: prompt title
               - prompt: prompt content
               - created_at: timestamp of creation

       Raises:
           Any database or service exceptions are propagated to the global exception handler.
       """
    service = PromptOptimizerService(db)
    prompt_info = service.save_prompt(
        tenant_id=current_user.tenant_id,
        session_id=data.session_id,
        title=data.title,
        prompt=data.prompt
    )
    return success(data=prompt_info)


@router.delete(
    "/releases/{prompt_id}",
    summary="Delete prompt (soft delete)",
    response_model=ApiResponse
)
def delete_prompt(
        prompt_id: uuid.UUID = Path(..., description="Prompt ID"),
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """
    Soft delete a prompt release.

    Args:
        prompt_id
        db (Session): Database session
        current_user: Current logged-in user

    Returns:
        ApiResponse: Success message confirming deletion
    """
    service = PromptOptimizerService(db)
    service.delete_prompt(
        tenant_id=current_user.tenant_id,
        prompt_id=prompt_id
    )
    return success(msg="Prompt deleted successfully")


@router.get(
    "/releases/list",
    summary="Get paginated list of released prompts with optional filter",
    response_model=ApiResponse
)
def get_release_list(
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """
    Retrieve paginated list of released prompts for the current tenant.
    Optionally filter by keyword in title.

    Args:
        page (int): Page number (starting from 1)
        page_size (int): Number of items per page (max 100)
        keyword (str | None): Optional keyword to filter prompt titles
        db (Session): Database session
        current_user: Current logged-in user

    Returns:
        ApiResponse: Contains paginated list of prompt releases with metadata
    """
    service = PromptOptimizerService(db)
    result = service.get_release_list(
        tenant_id=current_user.tenant_id,
        page=max(1, page),
        page_size=min(max(1, page_size), 100),
        filter_keyword=keyword
    )
    return success(data=result)


