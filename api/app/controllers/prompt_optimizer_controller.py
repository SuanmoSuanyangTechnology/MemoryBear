import uuid

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

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
    service.create_message(
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        user_id=current_user.id,
        role=RoleType.USER,
        content=data.message
    )
    opt_result = await service.optimize_prompt(
        tenant_id=current_user.tenant_id,
        model_id=data.model_id,
        session_id=session_id,
        user_id=current_user.id,
        current_prompt=data.current_prompt,
        message=data.message
    )
    service.create_message(
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        user_id=current_user.id,
        role=RoleType.ASSISTANT,
        content=opt_result.desc
    )
    variables = service.parser_prompt_variables(opt_result.prompt)
    result = {
        "prompt": opt_result.prompt,
        "desc": opt_result.desc,
        "variables": variables
    }
    result_schema = OptimizePromptResponse.model_validate(result)
    return success(data=result_schema)


@router.put(
    "/model",
    summary="Create or update prompt model config",
    response_model=ApiResponse
)
def set_system_prompt(
        data: PromptOptModelSet = ...,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """
    Create or update a system prompt model configuration for the tenant.

    Args:
        data (PromptOptModelSet): Model configuration data including model ID,
                                   system prompt, and optional configuration ID
        db (Session): Database session
        current_user: Current user information

    Returns:
        UUID: The ID of the created or updated model configuration.
    """
    if data.id is None:
        data.id = uuid.uuid4()

    model_config = PromptOptimizerService(db).create_update_model_config(
        current_user.tenant_id,
        data.id,
        data.system_prompt
    )
    return success(data=model_config.id)

