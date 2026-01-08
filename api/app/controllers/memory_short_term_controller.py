from fastapi import APIRouter, Depends, HTTPException, status
from app.core.logging_config import get_api_logger

from app.core.response_utils import success, fail
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.memory_repository import LongTermMemoryRepository
from app.repositories.memory_repository import ShortTermMemoryRepository
from app.services.memory_storage_service import search_entity
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User

from app.services.memory_storage_service import search_entity
from app.services.memory_short_service import ShortService,LongService
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from typing import Optional
load_dotenv()
api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/short",
    tags=["Memory"],
)
@router.get("/short_term")
async def short_term_configs(
        end_user_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
):
    # 获取短期记忆数据

    short_repo = ShortTermMemoryRepository(db)
    short_memories = short_repo.get_latest_by_user_id(end_user_id, 3)
    short_count=short_repo.count_by_user_id(end_user_id)

    short_result = []
    for memory in short_memories:
        deep_expanded = {}  # Create a new dictionary for each memory
        messages = memory.messages
        aimessages = memory.aimessages
        retrieved_content = memory.retrieved_content or []

        api_logger.debug(f"Retrieved content: {retrieved_content}")

        retrieval_source = []
        for item in retrieved_content:
            if isinstance(item, dict):
                for key, values in item.items():
                    retrieval_source.append({"query": key, "retrieval": values})

        deep_expanded['retrieval'] = retrieval_source
        deep_expanded['message'] = messages  # 修正拼写错误
        deep_expanded['answer'] = aimessages
        short_result.append(deep_expanded)

    # 获取长期记忆数据
    long_repo = LongTermMemoryRepository(db)
    long_memories = long_repo.get_by_user_id(end_user_id, 1)

    long_result = []
    for long_memory in long_memories:
        if long_memory.retrieved_content:
            for memory_item in long_memory.retrieved_content:
                if isinstance(memory_item, dict):
                    for key, values in memory_item.items():
                        long_result.append({"query": key, "retrieval": values,"source":"上下文对话"})

    short_term=ShortService(end_user_id)
    short_result=short_term.get_short_databasets()
    short_count=short_term.get_short_count()

    long_term=LongService(end_user_id)
    long_result=long_term.get_long_databasets()


    entity_result = await search_entity(end_user_id)
    result = {
        'short_term': short_result,
        'long_term': long_result,
        'entity': entity_result.get('num', 0),
        "retrieval_number":short_count,
        "long_term_number":len(long_result)
    }

    return success(data=result, msg="短期记忆系统数据获取成功")

