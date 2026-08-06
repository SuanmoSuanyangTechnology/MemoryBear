"""记忆展示控制器

提供写入展示记录、读取展示卡片和引擎动态卡片的分页查询接口，
以及把三者合并为一条时间线统一分页的聚合接口 /all。
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

# /all 做整体分页，但跨三类数据统一排序必须先把它们取到内存里合并，
# 因此对单类的取回条数设上限，防止响应构造无界增长。
# 某类超过该上限时会打 warning，此时深页取不到被截断的部分。
ALL_ITEM_LIMIT = 1000


@router.get("/all", response_model=ApiResponse)
async def get_all_memory_display(
    end_user_id: str = Query(..., description="终端用户 ID"),
    timezone: str = Header(
        ...,
        alias="X-Timezone",
        description="IANA 时区名称，前端必传 useI18n().timeZone，如 Asia/Shanghai",
    ),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    include_engines: bool = Query(
        True,
        description="是否包含引擎动态卡片（engines）。false 时不查询该类数据，"
                    "合并列表和 total 都不含它",
    ),
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """一次获取写入记录、读取卡片和引擎动态卡片

    与 /written、/retrieved、/engines 一样只有一份整体分页：三类数据合并成
    一条按 occurred_at 倒序的列表统一分页，total 是各类条数之和。
    每项新增 source 字段（written / retrieved / engines）标识来源，
    其余字段与对应单接口逐字一致。

    include_engines=false 时完全跳过引擎动态查询，返回的列表和 total 只含
    written 和 retrieved；written 和 retrieved 无法排除，它们是本接口的基本内容。

    其余参数语义与三个单接口完全一致：
    - X-Timezone 只影响 engines 的自然日聚合边界，与 /engines 一样必传；
      即使 include_engines=false 也要传，保持请求头契约稳定；
    - X-Language-Type 只影响 engines 的 name/content；written 的 name/content
      和 retrieved 的 query/content 都保持数据生成时的原始语言。
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

        written_result = MemoryDisplayRecordService.query_written(
            db=db,
            end_user_id=end_user_uuid,
            workspace_id=workspace_id,
            page=1,
            pagesize=ALL_ITEM_LIMIT,
        )
        retrieved_result = MemoryRetrievalDisplayService.query_retrieved(
            db=db,
            end_user_id=end_user_uuid,
            workspace_id=workspace_id,
            page=1,
            pagesize=ALL_ITEM_LIMIT,
        )
        engines_result = None
        if include_engines:
            engines_result = MemoryEngineDisplayService.query_cards(
                db=db,
                end_user_id=end_user_uuid,
                workspace_id=workspace_id,
                timezone=tz_name,
                language=language,
                page=1,
                pagesize=ALL_ITEM_LIMIT,
            )

        # 三个 Service 都用同一个归属校验，任一为 None 说明用户不在当前工作空间
        if (
            written_result is None
            or retrieved_result is None
            or (include_engines and engines_result is None)
        ):
            return fail(
                BizCode.USER_NOT_FOUND,
                "终端用户不存在",
                "end_user not found in current workspace",
            )

        blocks = {
            "written": written_result,
            "retrieved": retrieved_result,
        }
        if include_engines:
            blocks["engines"] = engines_result

        merged_items: list[dict] = []
        total = 0
        for source, (items, block_total) in blocks.items():
            total += block_total
            merged_items.extend({**item, "source": source} for item in items)
            if block_total > len(items):
                api_logger.warning(
                    f"展示记录聚合查询被截断: end_user_id={end_user_id}, "
                    f"source={source}, fetched={len(items)}, total={block_total}, "
                    f"limit={ALL_ITEM_LIMIT}"
                )

        # occurred_at 倒序；同一时刻按 source、id 定序，保证翻页结果稳定
        merged_items.sort(
            key=lambda item: (
                -(item.get("occurred_at") or 0),
                item["source"],
                str(item.get("id") or ""),
            )
        )

        offset = (page - 1) * pagesize
        page_meta = PageMeta(
            page=page,
            pagesize=pagesize,
            total=total,
            hasnext=(page * pagesize < total),
        )

        return success(
            data=PageData(
                page=page_meta,
                items=merged_items[offset:offset + pagesize],
            ),
            msg="查询成功",
        )

    except Exception as e:
        api_logger.error(
            f"展示记录聚合查询失败: end_user_id={end_user_id}, error={e}",
            exc_info=True,
        )
        return fail(BizCode.INTERNAL_ERROR, "展示记录查询失败", str(e))


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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取读取展示卡片列表

    一次用户可见的记忆检索对应一条记录，按 occurred_at 倒序分页。

    search_mode 始终返回稳定英文枚举，由前端负责展示文案映射；content
    是检索发生时按当时记忆语言聚合的快照，查询时不再翻译。
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
        query_result = MemoryRetrievalDisplayService.query_retrieved(
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
