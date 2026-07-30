"""记忆展示记录控制器

提供写入展示记录的分页查询接口。
"""

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.core.utils.datetime_utils import to_timestamp_ms
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.memory_display_record_repository import (
    MemoryDisplayRecordRepository,
)
from app.schemas.memory_episodic_schema import translate_episodic_type
from app.schemas.response_schema import ApiResponse, PageData, PageMeta

api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory-display",
    tags=["Memory Display"],
)


@router.get("/written", response_model=ApiResponse)
async def get_written_memories(
    end_user_id: str = Query(..., description="终端用户 ID"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取写入展示记录列表

    返回指定用户的写入记忆展示记录，按 created_at 倒序分页。

    memory_type 根据 X-Language-Type 返回中文文案或英文枚举；name 和
    content 保持记忆生成时的原始语言。
    """
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(
            BizCode.INVALID_PARAMETER,
            "请先切换到一个工作空间",
            "current_workspace_id is None",
        )

    if not end_user_id or not end_user_id.strip():
        return fail(
            BizCode.MISSING_PARAMETER,
            "end_user_id 不能为空",
            "end_user_id is required",
        )

    try:
        language = get_language_from_header(language_type)
        repo = MemoryDisplayRecordRepository(db)
        items, total = repo.query_written_paginated(
            end_user_id=end_user_id.strip(),
            page=page,
            pagesize=pagesize,
        )

        # 转换为前端 DTO
        result_items = []
        for record in items:
            result_items.append({
                "id": str(record.id),
                "memory_id": record.memory_id,
                "memory_type": translate_episodic_type(
                    record.memory_type,
                    language,
                ),
                "name": record.name,
                "content": record.content,
                "created_at": to_timestamp_ms(record.created_at),
            })

        page_meta = PageMeta(
            page=page,
            pagesize=pagesize,
            total=total,
            hasnext=(page * pagesize < total),
        )

        return success(
            data=PageData(page=page_meta, items=result_items),
            msg="查询成功",
        )

    except Exception as e:
        api_logger.error(
            f"写入展示记录查询失败: end_user_id={end_user_id}, error={e}",
            exc_info=True,
        )
        return fail(BizCode.INTERNAL_ERROR, "写入展示记录查询失败", str(e))
