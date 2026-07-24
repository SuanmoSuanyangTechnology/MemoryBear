from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_db, get_db_context
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.memory_storage_schema import (
    PilotRunInput,
)
from app.schemas.response_schema import ApiResponse
from app.services.memory_storage_service import (
    HOT_MEMORY_TAGS_CACHE_EXPIRE,
    DataConfigService,
    MemoryStorageService,
    analytics_hot_memory_tags,
    analytics_recent_activity_stats,
    kb_type_distribution,
    search_all_batch,
    search_chunk,
    search_detials,
    search_dialogue,
    search_edges,
    search_entity,
    search_statement,
)
from fastapi import Header

from app.utils.config_utils import resolve_config_id

# Get API logger
api_logger = get_api_logger()

# Initialize service
memory_storage_service = MemoryStorageService()

router = APIRouter(
    prefix="/memory-storage",
    tags=["Memory Storage"],
)


@router.get("/info", response_model=ApiResponse)
async def get_storage_info(
        storage_id: str,
        current_user: User = Depends(get_current_user)
):
    """
    Example wrapper endpoint - retrieves storage information
    
    Args:
        storage_id: Storage identifier
    
    Returns:
        Storage information
    """
    api_logger.info("Storage info requested ")
    try:
        result = await memory_storage_service.get_storage_info()
        return success(data=result)
    except Exception as e:
        api_logger.error(f"Storage info retrieval failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "存储信息获取失败", str(e))


# ==================== 记忆配置接口已迁移 ====================
# create_config / delete_config / update_config / update_config_extracted /
# read_config_extracted / read_all_config 已迁移至 memory_config_controller
# （前缀 /memory_config），此处不再保留。


@router.post("/pilot_run", response_model=None)
async def pilot_run(
        payload: PilotRunInput,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    # 使用集中化的语言校验
    language = get_language_from_header(language_type)

    # v0.3.13: 入参改为 QA 格式 messages（与 /write 接口对齐），不再接收 dialogue_text / custom_text
    total_files = sum(len(m.files) for m in payload.messages if m.files)
    api_logger.info(
        f"Pilot run requested: config_id={payload.config_id}, "
        f"messages_count={len(payload.messages)}, files_count={total_files}"
    )
    # resolve_config_id 在 pilot_run_stream 内部的短 session 里执行
    # controller 层不再注入 db，避免 session 跨越整个流式响应生命周期
    payload.config_id = resolve_config_id(payload.config_id)
    svc = DataConfigService()
    return StreamingResponse(
        svc.pilot_run_stream(payload, language=language),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== Search & Analytics ====================

@router.get("/search/kb_type_distribution", response_model=ApiResponse)
async def get_kb_type_distribution(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
) -> dict:
    api_logger.info(f"KB type distribution requested for end_user_id: {end_user_id}")
    try:
        result = await kb_type_distribution(end_user_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"KB type distribution failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "知识库类型分布查询失败", str(e))


@router.get("/search/dialogue", response_model=ApiResponse)
async def search_dialogues_num(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
) -> dict:
    api_logger.info(f"Search dialogue requested for end_user_id: {end_user_id}")
    try:
        result = await search_dialogue(end_user_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Search dialogue failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "对话查询失败", str(e))


@router.get("/search/chunk", response_model=ApiResponse)
async def search_chunks_num(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
) -> dict:
    api_logger.info(f"Search chunk requested for end_user_id: {end_user_id}")
    try:
        result = await search_chunk(end_user_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Search chunk failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "分块查询失败", str(e))


@router.get("/search/statement", response_model=ApiResponse)
async def search_statements_num(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
) -> dict:
    api_logger.info(f"Search statement requested for end_user_id: {end_user_id}")
    try:
        result = await search_statement(end_user_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Search statement failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "语句查询失败", str(e))


@router.get("/search/entity", response_model=ApiResponse)
async def search_entities_num(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
) -> dict:
    api_logger.info(f"Search entity requested for end_user_id: {end_user_id}")
    try:
        result = await search_entity(end_user_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Search entity failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "实体查询失败", str(e))


@router.get("/search", response_model=ApiResponse)
async def search_all_num(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
) -> dict:
    api_logger.info(f"Search all requested for end_user_id: {end_user_id}")
    try:
        if not end_user_id:
            return success(data={"total": 0}, msg="查询成功")
        batch_result = await search_all_batch([end_user_id])
        result = {"total": batch_result.get(end_user_id, 0)}
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Search all failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "全部查询失败", str(e))


@router.get("/search/detials", response_model=ApiResponse)
async def search_entities_detials(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
) -> dict:
    api_logger.info(f"Search details requested for end_user_id: {end_user_id}")
    try:
        result = await search_detials(end_user_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Search details failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "详情查询失败", str(e))


@router.get("/search/edges", response_model=ApiResponse)
async def search_entity_edges(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
) -> dict:
    api_logger.info(f"Search edges requested for end_user_id: {end_user_id}")
    try:
        result = await search_edges(end_user_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Search edges failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "边查询失败", str(e))


@router.get("/analytics/hot_memory_tags", response_model=ApiResponse)
async def get_hot_memory_tags_api(
        limit: int = 10,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
) -> dict:
    """
    获取热门记忆标签（带Redis缓存）
    
    缓存策略：
    - 缓存键：workspace_id + limit
    - 过期时间：28小时（HOT_MEMORY_TAGS_CACHE_EXPIRE），由每日定时任务预热刷新
    - 缓存命中：~50ms
    - 缓存未命中：~600-800ms（取决于LLM速度），实时查询后回写缓存作为兜底
    """
    workspace_id = current_user.current_workspace_id

    # 构建缓存键
    cache_key = f"hot_memory_tags:{workspace_id}:{limit}"

    api_logger.info(f"Hot memory tags requested for workspace: {workspace_id}, limit: {limit}")

    try:
        # 尝试从Redis缓存获取
        import json

        from app.aioRedis import aio_redis_get, aio_redis_set

        cached_result = await aio_redis_get(cache_key)
        if cached_result:
            api_logger.info(f"Cache hit for key: {cache_key}")
            try:
                data = json.loads(cached_result)
                return success(data=data, msg="查询成功（缓存）")
            except json.JSONDecodeError:
                api_logger.warning(f"Failed to parse cached data, will refresh")

        # 缓存未命中，执行查询
        api_logger.info(f"Cache miss for key: {cache_key}, executing query")
        result = await analytics_hot_memory_tags(db, current_user, limit)

        # 写入缓存（过期时间：28小时）
        # 注意：result是列表，需要转换为JSON字符串
        try:
            cache_data = json.dumps(result, ensure_ascii=False)
            await aio_redis_set(cache_key, cache_data, expire=HOT_MEMORY_TAGS_CACHE_EXPIRE)
            api_logger.info(f"Cached result for key: {cache_key}")
        except Exception as cache_error:
            # 缓存写入失败不影响主流程
            api_logger.warning(f"Failed to cache result: {str(cache_error)}")

        return success(data=result, msg="查询成功")

    except Exception as e:
        api_logger.error(f"Hot memory tags failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "热门标签查询失败", str(e))


@router.delete("/analytics/hot_memory_tags/cache", response_model=ApiResponse)
async def clear_hot_memory_tags_cache(
        current_user: User = Depends(get_current_user),
) -> dict:
    """
    清除热门标签缓存
    
    用于：
    - 手动刷新数据
    - 调试和测试
    - 数据更新后立即生效
    """
    workspace_id = current_user.current_workspace_id

    api_logger.info(f"Clear hot memory tags cache requested for workspace: {workspace_id}")

    try:
        from app.aioRedis import aio_redis_delete

        # 清除所有limit的缓存（常见的limit值）
        cleared_count = 0
        for limit in [5, 10, 15, 20, 30, 50]:
            cache_key = f"hot_memory_tags:{workspace_id}:{limit}"
            result = await aio_redis_delete(cache_key)
            if result:
                cleared_count += 1
                api_logger.info(f"Cleared cache for key: {cache_key}")

        return success(
            data={"cleared_count": cleared_count},
            msg=f"成功清除 {cleared_count} 个缓存"
        )

    except Exception as e:
        api_logger.error(f"Clear cache failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "清除缓存失败", str(e))


@router.get("/analytics/recent_activity_stats", response_model=ApiResponse)
async def get_recent_activity_stats_api(
        current_user: User = Depends(get_current_user),
) -> dict:
    workspace_id = str(current_user.current_workspace_id) if current_user.current_workspace_id else None
    api_logger.info(f"Recent activity stats requested: workspace_id={workspace_id}")
    try:
        result = await analytics_recent_activity_stats(workspace_id=workspace_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Recent activity stats failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "最近活动统计失败", str(e))


@router.delete("/end-users/{end_user_id}", response_model=ApiResponse)
async def delete_end_user(
        end_user_id: UUID,
        current_user: User = Depends(get_current_user),
) -> dict:
    """删除终端用户的全部记忆数据（Neo4j 节点 + PostgreSQL 记录）。

    1. DETACH DELETE 清除 Neo4j 中该用户所有节点和边
    2. memory_count 归零
    3. 软删除 end_user 记录（is_active=False）

    这是一个危险操作，不可撤销。
    """
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试删除终端用户但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    end_user_id_str = str(end_user_id)

    api_logger.info(
        f"删除终端用户请求: end_user_id={end_user_id_str}, "
        f"user={current_user.username}, workspace={workspace_id}"
    )

    try:
        from app.repositories.end_user_repository import EndUserRepository

        with get_db_context() as db:
            end_user = EndUserRepository(db).get_end_user_by_id(end_user_id)
            if not end_user:
                api_logger.warning(f"终端用户不存在或已删除: end_user_id={end_user_id_str}")
                return fail(BizCode.NOT_FOUND, "终端用户不存在或已删除", f"end_user_id={end_user_id_str}")
            if str(end_user.workspace_id) != str(workspace_id):
                api_logger.warning(
                    f"用户 {current_user.username} 尝试删除不属于工作空间 {workspace_id} 的终端用户 {end_user_id_str}"
                )
                return fail(BizCode.PERMISSION_DENIED, "该终端用户不属于当前工作空间", "end_user workspace mismatch")

        from app.core.memory.memory_service import MemoryService

        total_deleted = await MemoryService.delete_all_nodes_by_end_user_id(end_user_id_str)

        try:
            with get_db_context() as db:
                repo = EndUserRepository(db)
                repo.update_memory_count(end_user_id, 0)
                repo.soft_delete_by_end_user_id(end_user_id)
        except Exception as sync_err:
            api_logger.warning(f"同步 end_user 失败（不影响 Neo4j 删除结果）: {sync_err}")

        api_logger.info(
            f"终端用户删除完成: end_user_id={end_user_id_str}, total_deleted={total_deleted}"
        )
        return success(
            data={"deleted": True, "end_user_id": end_user_id_str, "total_deleted": total_deleted},
            msg=f"删除用户{end_user_id_str}记忆库成功"
        )

    except Exception as e:
        api_logger.error(f"删除终端用户失败: end_user_id={end_user_id_str}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "删除终端用户失败", str(e))
