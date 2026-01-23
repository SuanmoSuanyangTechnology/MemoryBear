import os
from typing import Optional
from uuid import UUID

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.memory_storage_schema import (
    ConfigKey,
    ConfigParamsCreate,
    ConfigParamsDelete,
    ConfigPilotRun,
    ConfigUpdate,
    ConfigUpdateExtracted,
)
from app.schemas.response_schema import ApiResponse
from app.services.memory_storage_service import (
    DataConfigService,
    MemoryStorageService,
    analytics_hot_memory_tags,
    analytics_recent_activity_stats,
    kb_type_distribution,
    search_all,
    search_chunk,
    search_detials,
    search_dialogue,
    search_edges,
    search_entity,
    search_statement,
)
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

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


# --- DB connection dependency ---
_CONN: Optional[object] = None


"""PostgreSQL 连接生成与管理（使用 psycopg2）。"""
# 这个可以转移，可能是已经有的
# PostgreSQL 数据库连接
def _make_pgsql_conn() -> Optional[object]:  # 创建 PostgreSQL 数据库连接
    host = os.getenv("DB_HOST")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    database = os.getenv("DB_NAME")
    port_str = os.getenv("DB_PORT")
    try:
        import psycopg2  # type: ignore
        port = int(port_str) if port_str else 5432
        conn = psycopg2.connect(
            host=host or "localhost",
            port=port,
            user=user,
            password=password,
            dbname=database,
        )
        # 设置自动提交，避免显式事务管理
        conn.autocommit = True
        # 设置会话时区为中国标准时间（Asia/Shanghai），便于直接以本地时区展示
        try:
            cur = conn.cursor()
            cur.execute("SET TIME ZONE 'Asia/Shanghai'")
            cur.close()
        except Exception:
            # 时区设置失败不影响连接，仅记录但不抛出
            pass
        return conn
    except Exception as e:
        try:
            print(f"[PostgreSQL] 连接失败: {e}")
        except Exception:
            pass
        return None

def get_db_conn() -> Optional[object]:  # 获取 PostgreSQL 数据库连接
    global _CONN
    if _CONN is None:
        _CONN = _make_pgsql_conn()
    return _CONN


def reset_db_conn() -> bool:  # 重置 PostgreSQL 数据库连接
    """Close and recreate the global DB connection."""
    global _CONN
    try:
        if _CONN:
            try:
                _CONN.close()
            except Exception:
                pass
        _CONN = _make_pgsql_conn()
        return _CONN is not None
    except Exception:
        _CONN = None
        return False


@router.post("/create_config", response_model=ApiResponse)   # 创建配置文件，其他参数默认
def create_config(
    payload: ConfigParamsCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试创建配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求创建配置: {payload.config_name}")
    try:
        # 将 workspace_id 注入到 payload 中（保持为 UUID 类型）
        payload.workspace_id = workspace_id
        svc = DataConfigService(db)
        result = svc.create(payload)
        return success(data=result, msg="创建成功")
    except Exception as e:
        api_logger.error(f"Create config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "创建配置失败", str(e))


@router.delete("/delete_config", response_model=ApiResponse)  # 删除数据库中的内容（按配置名称）
def delete_config(
    config_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试删除配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求删除配置: {config_id}")
    try:
        svc = DataConfigService(db)
        result = svc.delete(ConfigParamsDelete(config_id=config_id))
        return success(data=result, msg="删除成功")
    except Exception as e:
        api_logger.error(f"Delete config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "删除配置失败", str(e))

@router.post("/update_config", response_model=ApiResponse)  # 更新配置文件中name和desc
def update_config(
    payload: ConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新配置: {payload.config_id}")
    try:
        svc = DataConfigService(db)
        result = svc.update(payload)
        return success(data=result, msg="更新成功")
    except Exception as e:
        api_logger.error(f"Update config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新配置失败", str(e))


@router.post("/update_config_extracted", response_model=ApiResponse)  # 更新数据库中的部分内容 所有业务字段均可选
def update_config_extracted(
    payload: ConfigUpdateExtracted,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新提取配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新提取配置: {payload.config_id}")
    try:
        svc = DataConfigService(db)
        result = svc.update_extracted(payload)
        return success(data=result, msg="更新成功")
    except Exception as e:
        api_logger.error(f"Update config extracted failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新配置失败", str(e))


# --- Forget config params ---
# 遗忘引擎配置接口已迁移到 memory_forget_controller.py
# 使用新接口: /api/memory/forget/read_config 和 /api/memory/forget/update_config

@router.get("/read_config_extracted", response_model=ApiResponse) # 通过查询参数读取某条配置（固定路径） 没有意义的话就删除
def read_config_extracted(
    config_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试读取提取配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取提取配置: {config_id}")
    try:
        svc = DataConfigService(db)
        result = svc.get_extracted(ConfigKey(config_id=config_id))
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Read config extracted failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询配置失败", str(e))

@router.get("/read_all_config", response_model=ApiResponse) # 读取所有配置文件列表
def read_all_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取所有配置")
    try:
        svc = DataConfigService(db)
        # 传递 workspace_id 进行过滤（保持为 UUID 类型）
        result = svc.get_all(workspace_id=workspace_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Read all config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询所有配置失败", str(e))


@router.post("/pilot_run", response_model=None)
async def pilot_run(
    payload: ConfigPilotRun,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    api_logger.info(
        f"Pilot run requested: config_id={payload.config_id}, "
        f"dialogue_text_length={len(payload.dialogue_text)}"
    )
    svc = DataConfigService(db)
    return StreamingResponse(
        svc.pilot_run_stream(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

"""
以下为搜索与分析接口，直接挂载到同一 router，统一响应为 ApiResponse。
"""

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
        result = await search_all(end_user_id)
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
    - 过期时间：5分钟（300秒）
    - 缓存命中：~50ms
    - 缓存未命中：~600-800ms（取决于LLM速度）
    """
    workspace_id = current_user.current_workspace_id
    
    # 构建缓存键
    cache_key = f"hot_memory_tags:{workspace_id}:{limit}"
    
    api_logger.info(f"Hot memory tags requested for workspace: {workspace_id}, limit: {limit}")
    
    try:
        # 尝试从Redis缓存获取
        from app.aioRedis import aio_redis_get, aio_redis_set
        import json
        
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
        
        # 写入缓存（过期时间：5分钟）
        # 注意：result是列表，需要转换为JSON字符串
        try:
            cache_data = json.dumps(result, ensure_ascii=False)
            await aio_redis_set(cache_key, cache_data, expire=300)
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
    api_logger.info("Recent activity stats requested")
    try:
        result = await analytics_recent_activity_stats()
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Recent activity stats failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "最近活动统计失败", str(e))

