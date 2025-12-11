from pydantic import BaseModel

from app.core.agent.agent_chat import Agent_chat
from app.core.logging_config import get_business_logger
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import workspace_access_guard
from app.services.agent_server import config,ChatRequest
router = APIRouter(prefix="/Test", tags=["Apps"])
logger = get_business_logger()
class CombinedRequest(BaseModel):
    config_base: config
    agent_config: ChatRequest

@router.post("", summary="uuid")
async def agent_chat(
    config_base: CombinedRequest
):
    chat_config=config_base.agent_config
    chat_base=config_base.config_base
    request = ChatRequest(
    end_user_id=chat_config.end_user_id,
    message=chat_config.message,
    search_switch=chat_config.search_switch,
    kb_ids=chat_config.kb_ids,
    similarity_threshold=chat_config.similarity_threshold,
    vector_similarity_weight=chat_config.vector_similarity_weight,
    top_k=chat_config.top_k,
    hybrid=chat_config.hybrid,
    token=chat_config.token
    )

    chat_result=await Agent_chat(chat_base).chat(request)

    return chat_result
