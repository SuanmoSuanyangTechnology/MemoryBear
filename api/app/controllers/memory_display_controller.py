"""记忆展示控制器

提供写入展示记录、读取展示卡片和引擎动态卡片的分页查询接口。
"""

import uuid

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.core.utils.datetime_utils import resolve_iana_timezone
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse, PageData, PageMeta
from app.services.memory_display_record_service import MemoryDisplayRecordService
from app.services.memory_engine_display_service import MemoryEngineDisplayService
from app.services.memory_retrieval_display_service import MemoryRetrievalDisplayService

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取写入展示记录列表

    返回指定用户的写入记忆展示记录，按 occurred_at 倒序分页。

    memory_type 始终返回稳定英文枚举，由前端负责展示文案映射；name 和
    content 保持记忆生成时的原始语言，不受 X-Language-Type 影响。
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

    normalized_end_user_id = end_user_id.strip()
    try:
        end_user_uuid = uuid.UUID(normalized_end_user_id)
    except (ValueError, AttributeError):
        return fail(
            BizCode.INVALID_PARAMETER,
            "无效的 end_user_id",
            f"'{normalized_end_user_id}' is not a valid UUID",
        )

    try:
        query_result = MemoryDisplayRecordService.query_written(
            db=db,
            end_user_id=end_user_uuid,
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
        )
        if query_result is None:
            return fail(
                BizCode.USER_NOT_FOUND,
                "终端用户不存在",
                "end_user not found in current workspace",
            )
        result_items, total = query_result

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


@router.get("/retrieved", response_model=ApiResponse)
async def get_retrieved_memories(
    end_user_id: str = Query(..., description="终端用户 ID"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取读取展示卡片列表

    一次用户可见的记忆检索对应一条记录，按 occurred_at 倒序分页。

    X-Language-Type 只决定 search_mode 的展示文案；content 在检索发生时
    已按当时的记忆语言聚合为快照，查询时不再翻译其中的“相关内容 / Related”文案。
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

    normalized_end_user_id = end_user_id.strip()
    try:
        end_user_uuid = uuid.UUID(normalized_end_user_id)
    except (ValueError, AttributeError):
        return fail(
            BizCode.INVALID_PARAMETER,
            "无效的 end_user_id",
            f"'{normalized_end_user_id}' is not a valid UUID",
        )

    try:
        language = get_language_from_header(language_type)
        query_result = MemoryRetrievalDisplayService.query_retrieved(
            db=db,
            end_user_id=end_user_uuid,
            workspace_id=workspace_id,
            language=language,
            page=page,
            pagesize=pagesize,
        )
        if query_result is None:
            return fail(
                BizCode.USER_NOT_FOUND,
                "终端用户不存在",
                "end_user not found in current workspace",
            )
        result_items, total = query_result

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
            f"读取展示记录查询失败: end_user_id={end_user_id}, error={e}",
            exc_info=True,
        )
        return fail(BizCode.INTERNAL_ERROR, "读取展示记录查询失败", str(e))


@router.get("/engines", response_model=ApiResponse)
async def get_engine_display_cards(
    end_user_id: str = Query(..., description="终端用户 ID"),
    timezone: str = Header(
        ...,
        alias="X-Timezone",
        description="IANA 时区名称，前端必传 useI18n().timeZone，如 Asia/Shanghai",
    ),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取引擎动态展示卡片列表

    按"指定时区下的自然日 + 引擎类型"聚合事件并返回卡片。

    engine_type 始终返回 EXTRACTION、CROSS_MODAL 或 EMOTION，
    由前端负责展示文案映射；X-Language-Type 仅控制 name/content 文案。

    聚合边界必须在服务端确定，因此 X-Timezone 为必传请求头，
    前端统一传全局时区设置（useI18n().timeZone），
    保证卡片的聚合日期与前端展示 occurred_at 时使用的时区一致。
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

    normalized_end_user_id = end_user_id.strip()
    try:
        end_user_uuid = uuid.UUID(normalized_end_user_id)
    except (ValueError, AttributeError):
        return fail(
            BizCode.INVALID_PARAMETER,
            "无效的 end_user_id",
            f"'{normalized_end_user_id}' is not a valid UUID",
        )

    # 验证时区请求头（必传，非法值直接报错，避免按错误时区聚合）
    if not timezone or not timezone.strip():
        return fail(
            BizCode.MISSING_PARAMETER,
            "X-Timezone 不能为空",
            "X-Timezone header is required",
        )

    try:
        _, tz_name = resolve_iana_timezone(timezone)
    except ValueError as e:
        return fail(
            BizCode.INVALID_PARAMETER,
            f"无效的时区: {timezone.strip()}",
            str(e),
        )

    try:
        language = get_language_from_header(language_type)
        query_result = MemoryEngineDisplayService.query_cards(
            db=db,
            end_user_id=end_user_uuid,
            workspace_id=workspace_id,
            timezone=tz_name,
            language=language,
            page=page,
            pagesize=pagesize,
        )
        if query_result is None:
            return fail(
                BizCode.USER_NOT_FOUND,
                "终端用户不存在",
                "end_user not found in current workspace",
            )
        cards, total = query_result

        page_meta = PageMeta(
            page=page,
            pagesize=pagesize,
            total=total,
            hasnext=(page * pagesize < total),
        )

        return success(
            data=PageData(page=page_meta, items=cards),
            msg="查询成功",
        )

    except Exception as e:
        api_logger.error(
            f"引擎展示记录查询失败: end_user_id={end_user_id}, error={e}",
            exc_info=True,
        )
        return fail(BizCode.INTERNAL_ERROR, "引擎展示记录查询失败", str(e))
