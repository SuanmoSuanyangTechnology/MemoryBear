"""记忆分析（Analytics）服务接口 - 基于 JWT 认证

将原先散落在 user_memory_controllers（/memory-storage）与 memory_agent_controller
（/memory）中的用户记忆分析类接口，统一收口到 /memory/analytics 前缀下，
使对内 /api 路由与对外 /v1（user_memory_api_controller）保持一致。

路由前缀: /memory/analytics
认证方式: JWT Token
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query

from app.cache.memory.interest_memory import InterestMemoryCache
from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.memory.constants.graph_data_constants import (
    CENTER_MODE_LIMIT_HARD_MAX,
    DEPTH_HARD_MAX,
)
from app.core.response_utils import fail, success
from app.db import get_db_context
from app.dependencies import CurrentUserSnapshot, get_current_user_async
from app.schemas.memory_storage_schema import GenerateCacheRequest
from app.schemas.response_schema import ApiResponse
from app.services._graph_data_helpers import parse_per_type_limits
from app.services.memory_agent_service import MemoryAgentService
from app.services.user_memory_service import (
    UserMemoryService,
    analytics_community_graph_data,
    analytics_graph_data,
    analytics_memory_types_async,
)

api_logger = get_api_logger()

user_memory_service = UserMemoryService()
memory_agent_service = MemoryAgentService()

router = APIRouter(
    prefix="/memory/analytics",
    tags=["Memory Analytics"],
)


@router.get("/memory_insight", response_model=ApiResponse)
async def get_memory_insight_report_api(
        end_user_id: str,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    """获取缓存的记忆洞察报告"""
    api_logger.info(f"记忆洞察报告查询请求: end_user_id={end_user_id}, user={current_user.username}")
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    try:
        result = await user_memory_service.get_cached_memory_insight_async(
            end_user_id,
            workspace_id,
        )

        if result.get("is_cached"):
            return success(data=result, msg="查询成功")
        else:
            return success(data=result, msg="数据尚未生成")
    except Exception as e:
        api_logger.error(f"记忆洞察报告查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "记忆洞察报告查询失败", str(e))


@router.get("/user_summary", response_model=ApiResponse)
async def get_user_summary_api(
        end_user_id: str,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    """获取缓存的用户摘要"""
    api_logger.info(f"用户摘要查询请求: end_user_id={end_user_id}, user={current_user.username}")
    try:
        result = await user_memory_service.get_cached_user_summary_async(end_user_id)

        if result.get("is_cached"):
            return success(data=result, msg="查询成功")
        else:
            return success(data=result, msg="数据尚未生成")
    except Exception as e:
        api_logger.error(f"用户摘要查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "用户摘要查询失败", str(e))


@router.post("/generate_cache", response_model=ApiResponse)
async def generate_cache_api(
        request: GenerateCacheRequest,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    """
    手动触发缓存生成

    - 如果提供 end_user_id，只为该用户生成
    - 如果不提供，为当前工作空间的所有用户生成

    语言控制：
    - 使用 X-Language-Type Header 指定语言 ("zh" 中文, "en" 英文)
    - 如果未传 Header，默认使用中文 (zh)
    """
    language = get_language_from_header(language_type)

    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试生成缓存但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    end_user_id = request.end_user_id

    api_logger.info(
        f"缓存生成请求: user={current_user.username}, workspace={workspace_id}, "
        f"end_user_id={end_user_id if end_user_id else '全部用户'}, language={language}"
    )

    try:
        workspace_validation = await user_memory_service.validate_neo4j_cache_workspace(workspace_id)
        if not workspace_validation.valid:
            if workspace_validation.reason == "unsupported_storage_type":
                return fail(
                    BizCode.INVALID_PARAMETER,
                    "当前接口仅支持 Neo4j 工作空间；RAG 工作空间请使用 RAG 专用画像生成入口",
                    f"unsupported storage_type: {workspace_validation.storage_type or 'unknown'}",
                )
            return fail(
                BizCode.INVALID_PARAMETER,
                "当前工作空间不可用",
                workspace_validation.reason,
            )

        with get_db_context() as db:
            if end_user_id:
                api_logger.info(f"开始为单个用户生成缓存: end_user_id={end_user_id}")

                insight_result = await user_memory_service.generate_and_cache_insight(
                    end_user_id, workspace_id, language=language, db=db,
                )
                summary_result = await user_memory_service.generate_and_cache_summary(
                    end_user_id, workspace_id, language=language, db=db,
                )

                result = {
                    "end_user_id": end_user_id,
                    "insight_success": insight_result["success"],
                    "summary_success": summary_result["success"],
                    "errors": []
                }

                if not insight_result["success"]:
                    result["errors"].append({
                        "type": "insight",
                        "error": insight_result.get("error")
                    })
                if not summary_result["success"]:
                    result["errors"].append({
                        "type": "summary",
                        "error": summary_result.get("error")
                    })

                if result["insight_success"] and result["summary_success"]:
                    api_logger.info(f"成功为用户 {end_user_id} 生成缓存")
                    return success(data=result, msg="生成完成")

                # 至少一个失败：真实反映给调用方，data 里仍带上明细
                api_logger.warning(f"用户 {end_user_id} 的缓存生成部分失败: {result['errors']}")
                if not result["insight_success"] and not result["summary_success"]:
                    error_msg = "; ".join(
                        f"{item.get('type')}: {item.get('error')}"
                        for item in result["errors"]
                    ) or "unknown error"
                    return fail(
                        BizCode.INTERNAL_ERROR,
                        "缓存生成失败",
                        error_msg,
                        data=result,
                    )
                return success(data=result, msg="部分生成成功")

            else:
                api_logger.info(f"开始为工作空间 {workspace_id} 批量生成缓存")

                result = await user_memory_service.generate_cache_for_workspace(db, workspace_id, language=language)

                api_logger.info(
                    f"工作空间 {workspace_id} 批量生成完成: "
                    f"总数={result['total_users']}, 成功={result['successful']}, 失败={result['failed']}"
                )

                return success(data=result, msg="批量生成完成")

    except Exception as e:
        api_logger.error(f"缓存生成失败: user={current_user.username}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "缓存生成失败", str(e))


@router.get("/node_statistics", response_model=ApiResponse)
async def get_node_statistics_api(
        end_user_id: str,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询节点统计但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"记忆类型统计请求: end_user_id={end_user_id}, user={current_user.username}")

    try:
        result = await analytics_memory_types_async(end_user_id=end_user_id)

        total_count = sum(item["count"] for item in result)
        api_logger.info(
            f"成功获取记忆类型统计: end_user_id={end_user_id}, 总记忆数={total_count}, 类型数={len(result)}")
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"记忆类型查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "记忆类型查询失败", str(e))


@router.get("/graph_data", response_model=ApiResponse)
async def get_graph_data_api(
        end_user_id: str,
        node_types: Optional[str] = None,
        limit: int = 100,
        depth: int = 1,
        center_node_id: Optional[str] = None,
        per_type_limits: Optional[str] = None,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询图数据但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    if limit > CENTER_MODE_LIMIT_HARD_MAX:
        limit = CENTER_MODE_LIMIT_HARD_MAX
        api_logger.warning(f"limit 参数超过最大值，已调整为 {CENTER_MODE_LIMIT_HARD_MAX}")

    if depth > DEPTH_HARD_MAX:
        depth = DEPTH_HARD_MAX
        api_logger.warning(f"depth 参数超过最大值，已调整为 {DEPTH_HARD_MAX}")

    node_types_list = None
    if node_types:
        node_types_list = [t.strip() for t in node_types.split(",") if t.strip()]

    try:
        per_type_limits_map = parse_per_type_limits(per_type_limits, api_logger)
    except ValueError as e:
        api_logger.warning(f"per_type_limits 参数解析失败: raw={per_type_limits!r}, error={e}")
        return fail(BizCode.INVALID_PARAMETER, "per_type_limits 参数格式错误", str(e))

    if center_node_id and node_types_list:
        api_logger.info(
            "同时传入 center_node_id 与 node_types，按设计优先 Center_Mode，"
            f"node_types={node_types_list} 将被忽略"
        )

    api_logger.info(
        f"图数据查询请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}, node_types={node_types_list}, limit={limit}, depth={depth}, "
        f"center_node_id={center_node_id}, per_type_limits={per_type_limits_map}"
    )

    try:
        result = await analytics_graph_data(
            end_user_id=end_user_id,
            node_types=node_types_list,
            limit=limit,
            depth=depth,
            center_node_id=center_node_id,
            per_type_limits=per_type_limits_map,
        )
        if "message" in result and result["statistics"]["total_nodes"] == 0:
            api_logger.warning(f"图数据查询返回空结果: {result.get('message')}")
            return success(data=result, msg=result.get("message", "查询成功"))

        api_logger.info(
            f"成功获取图数据: end_user_id={end_user_id}, "
            f"nodes={result['statistics']['total_nodes']}, "
            f"edges={result['statistics']['total_edges']}"
        )
        return success(data=result, msg="查询成功")

    except Exception as e:
        api_logger.error(f"图数据查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "图数据查询失败", str(e))


@router.get("/community_graph", response_model=ApiResponse)
async def get_community_graph_data_api(
        end_user_id: str,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询社区图谱但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"社区图谱查询请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )

    try:
        result = await analytics_community_graph_data(end_user_id=end_user_id)

        if "message" in result and result["statistics"]["total_nodes"] == 0:
            api_logger.warning(f"社区图谱查询返回空结果: {result.get('message')}")
            return success(data=result, msg=result.get("message", "查询成功"))

        api_logger.info(
            f"成功获取社区图谱: end_user_id={end_user_id}, "
            f"nodes={result['statistics']['total_nodes']}, "
            f"edges={result['statistics']['total_edges']}"
        )
        return success(data=result, msg="查询成功")

    except Exception as e:
        api_logger.error(f"社区图谱查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "社区图谱查询失败", str(e))


# get_end_user_info 已迁移至 end_user_controller（/end_user/info）


@router.get("/interest_distribution", response_model=ApiResponse)
async def get_interest_distribution_by_user_api(
        end_user_id: str = Query(..., description="用户ID（必填）"),
        limit: int = Query(5, le=5, description="返回兴趣标签数量限制，最多5个"),
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """
    获取指定用户的兴趣分布标签

    与热门标签不同，此接口专注于识别用户的兴趣活动（运动、爱好、学习、创作等），
    过滤掉纯物品、工具、地点等不代表用户主动参与活动的名词。
    """
    language = get_language_from_header(language_type)
    api_logger.info(f"Interest distribution by user requested: end_user_id={end_user_id}, language={language}")
    try:
        cached = await InterestMemoryCache.get_interest_distribution(
            end_user_id=end_user_id,
            language=language,
        )
        if cached is not None:
            api_logger.info(f"Interest distribution cache hit: end_user_id={end_user_id}")
            return success(data=cached[:limit], msg="获取兴趣分布标签成功")

        result, cacheable = await memory_agent_service.generate_interest_distribution_by_user(
            end_user_id=end_user_id,
            limit=5,
            language=language
        )

        if cacheable:
            await InterestMemoryCache.set_interest_distribution(
                end_user_id=end_user_id,
                language=language,
                data=result,
            )
        else:
            api_logger.info(
                "Interest distribution not cached after empty LLM result: "
                f"end_user_id={end_user_id}"
            )

        return success(data=result[:limit], msg="获取兴趣分布标签成功")
    except Exception as e:
        api_logger.error(f"Interest distribution by user failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取兴趣分布标签失败", str(e))
