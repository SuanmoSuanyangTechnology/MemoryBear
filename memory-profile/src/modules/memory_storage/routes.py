"""Internal memory-storage routes mirroring the manager API."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.common.language_utils import get_language_from_header
from src.i18n import get_request_language
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.memory_storage.handler import MemoryStorageHandler
from src.schemas.memory_storage import (
    DeleteNodeRequest,
    HotMemoryTag,
    PilotRunInput,
    TrialRunChatInput,
)

router = APIRouter(prefix="/memory-storage", tags=["Memory Storage"])
#: 单体把热门标签声明在 memory_storage 控制器，但 API-Key 面的对外路径在分析域
#: （``/v1/memory/analytics/hot_memory_tags``）；同一实现在两个前缀下注册。
analytics_router = APIRouter(prefix="/memory/analytics", tags=["Profile Analytics"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> MemoryStorageHandler:
    return MemoryStorageHandler(request.app.state.runtime)


@router.get("/memory_space/timeline_memories", response_model=ApiResponse[Any])
async def memory_space_timeline_of_shared_memories(
    request: Request,
    context: Context,
    id: str,
    label: str,
    language_type: str = Header(default=None, alias="X-Language-Type"),
):
    # 使用集中化的语言校验
    language = get_language_from_header(language_type)
    return await _handler(request).timeline_memories(context, id, label, language)


@router.get("/memory_space/entity_timeline", response_model=ApiResponse[Any])
async def memory_space_entity_timeline(
    request: Request,
    context: Context,
    id: str | None = Query(None, description="实体 elementId，与 end_user_id 二选一"),
    type: str = Query("all", description="来源筛选：all/key_node/statement/memory_summary"),
    sort: str = Query("time_desc", description="排序方式：time_asc/time_desc/occurred_asc/occurred_desc/recorded_asc/recorded_desc"),
    from_ts: int = Query(None, description="时间范围起始（UTC 毫秒时间戳），由前端按用户时区转换后传入"),
    to_ts: int = Query(None, description="时间范围结束（UTC 毫秒时间戳），由前端按用户时区转换后传入"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    end_user_id: str | None = Query(None, description="终端用户 UUID 字符串，与 id 二选一"),
):
    """ExtractedEntity 合并记忆时间线（关键节点 / 来源陈述 / 长期沉淀），分页 + 按来源筛选 + 排序 + 日期过滤。

    本接口仅服务 ExtractedEntity 节点（只有它才有「关键节点」来源）；
    Statement / MemorySummary 节点仍走旧接口 timeline_memories。

    Query 参数:
        id: Optional ExtractedEntity elementId, mutually exclusive with end_user_id.
        end_user_id: Optional UUID string identifying the user's own entity.
        type: 来源筛选，all/key_node/statement/memory_summary，默认 all
        sort: 排序方式，默认 time_desc（时间倒序）
              time_asc/time_desc: 全部按 created_at 排序（key_node=发生时间，statement/memory_summary=记录时间）
              occurred_asc/occurred_desc: 仅 key_node 按发生时间排序，其余无发生时间排最后
              recorded_asc/recorded_desc: 仅 statement/memory_summary 按记录时间排序，key_node 无记录时间排最后
        from_ts: 时间范围起始（UTC 毫秒），前端 dayjs.tz(date, timezone).startOf('day').valueOf()
        to_ts: 时间范围结束（UTC 毫秒），前端 dayjs.tz(date, timezone).endOf('day').valueOf()
        page: 页码（从 1 开始）
        pagesize: 每页条数（1~100）

    total_count 与 type_stats 始终基于全量统计，不受筛选影响。
    filtered_counts counts key nodes and summaries after date filtering, before type filtering and pagination.
    """
    return await _handler(request).entity_timeline(
        context,
        id=id,
        end_user_id=end_user_id,
        type=type,
        sort=sort,
        from_ts=from_ts,
        to_ts=to_ts,
        page=page,
        pagesize=pagesize,
    )


@router.get("/memory_space/relationship_evolution", response_model=ApiResponse[Any])
async def memory_space_relationship_evolution(request: Request, context: Context, id: str, label: str):
    return await _handler(request).relationship_evolution(context, id, label)


@router.get("/analytics/hot_memory_tags", response_model=ApiResponse[list[HotMemoryTag]])
async def memory_storage_hot_memory_tags(
    request: Request,
    context: Context,
    limit: int = 10,
) -> ApiResponse[list[HotMemoryTag]]:
    """管理端历史前缀下的热门记忆标签（单体 ``memory_storage_controller`` 的路径）。"""

    return await _handler(request).hot_memory_tags(
        context, limit, get_request_language(request)
    )


@analytics_router.get("/hot_memory_tags", response_model=ApiResponse[list[HotMemoryTag]])
@external_api
async def hot_memory_tags(
    request: Request,
    context: Context,
    limit: int = 10,
) -> ApiResponse[list[HotMemoryTag]]:
    """工作空间热门记忆标签（API-Key 面的原始路径，进入外部契约快照）。"""

    return await _handler(request).hot_memory_tags(
        context, limit, get_request_language(request)
    )


@router.post("/pilot_run", response_model=None)
async def pilot_run(request: Request, context: Context, payload: PilotRunInput):
    language = get_language_from_header(request.headers.get("X-Language-Type"))
    stream = await _handler(request).pilot_run(context, payload, language)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat", response_model=None)
async def chat(request: Request, context: Context, payload: TrialRunChatInput):
    language = get_language_from_header(request.headers.get("X-Language-Type"))
    stream = await _handler(request).chat(context, payload, language)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/end-users/{end_user_id}", response_model=ApiResponse[Any])
async def delete_end_user(request: Request, context: Context, end_user_id: UUID):
    return await _handler(request).delete_end_user(context, end_user_id)


@router.post("/node/delete", response_model=ApiResponse[Any])
async def delete_node_api(request: Request, context: Context, payload: DeleteNodeRequest):
    return await _handler(request).delete_node(context, payload)
