from fastapi import APIRouter, Depends, HTTPException, status
from app.core.logging_config import get_api_logger
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