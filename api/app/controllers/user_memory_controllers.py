"""
用户记忆相关的控制器
包含用户摘要、记忆洞察、节点统计、图数据和用户档案等接口
"""
from typing import Optional
import datetime
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, Header

from app.db import get_db
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.response_utils import success, fail
from app.core.error_codes import BizCode
from app.core.api_key_utils import timestamp_to_datetime
from app.services.user_memory_service import (
    UserMemoryService,
    analytics_memory_types,
    analytics_graph_data,
    analytics_community_graph_data,
)
from app.services.memory_entity_relationship_service import MemoryEntityService, MemoryEmotion, MemoryInteraction
from app.schemas.response_schema import ApiResponse
from app.schemas.memory_storage_schema import GenerateCacheRequest
from app.repositories.workspace_repository import WorkspaceRepository
from app.schemas.end_user_schema import (
    UserAliasResponse,
    UserAliasCreate,
    UserAliasUpdate,
)
from app.models.end_user_model import EndUser
from app.dependencies import get_current_user
from app.models.user_model import User

# Get API logger
api_logger = get_api_logger()

# Initialize service
user_memory_service = UserMemoryService()

router = APIRouter(
    prefix="/memory-storage",
    tags=["User Memory"],
)


@router.get("/analytics/memory_insight/report", response_model=ApiResponse)
async def get_memory_insight_report_api(
        end_user_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """
    获取缓存的记忆洞察报告

    此接口仅查询数据库中已缓存的记忆洞察数据，不执行生成操作。
    如需生成新的洞察报告，请使用专门的生成接口。
    """
    api_logger.info(f"记忆洞察报告查询请求: end_user_id={end_user_id}, user={current_user.username}")
    try:
        # 调用服务层获取缓存数据
        result = await user_memory_service.get_cached_memory_insight(db, end_user_id)

        if result["is_cached"]:
            api_logger.info(f"成功返回缓存的记忆洞察报告: end_user_id={end_user_id}")
            return success(data=result, msg="查询成功")
        else:
            api_logger.info(f"记忆洞察报告缓存不存在: end_user_id={end_user_id}")
            return success(data=result, msg="数据尚未生成")
    except Exception as e:
        api_logger.error(f"记忆洞察报告查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "记忆洞察报告查询失败", str(e))


@router.get("/analytics/user_summary", response_model=ApiResponse)
async def get_user_summary_api(
        end_user_id: str,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """
    获取缓存的用户摘要

    此接口仅查询数据库中已缓存的用户摘要数据，不执行生成操作。
    如需生成新的用户摘要，请使用专门的生成接口。
    
    语言控制：
    - 使用 X-Language-Type Header 指定语言
    - 如果未传 Header，默认使用中文 (zh)
    """
    # 使用集中化的语言校验
    language = get_language_from_header(language_type)

    workspace_id = current_user.current_workspace_id
    workspace_repo = WorkspaceRepository(db)
    workspace_models = workspace_repo.get_workspace_models_configs(workspace_id)

    if workspace_models:
        model_id = workspace_models.get("llm", None)
    else:
        model_id = None
    api_logger.info(f"用户摘要查询请求: end_user_id={end_user_id}, user={current_user.username}")
    try:
        # 调用服务层获取缓存数据
        result = await user_memory_service.get_cached_user_summary(db, end_user_id, model_id, language)

        if result["is_cached"]:
            api_logger.info(f"成功返回缓存的用户摘要: end_user_id={end_user_id}")
            return success(data=result, msg="查询成功")
        else:
            api_logger.info(f"用户摘要缓存不存在: end_user_id={end_user_id}")
            return success(data=result, msg="数据尚未生成")
    except Exception as e:
        api_logger.error(f"用户摘要查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "用户摘要查询失败", str(e))


@router.post("/analytics/generate_cache", response_model=ApiResponse)
async def generate_cache_api(
        request: GenerateCacheRequest,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """
    手动触发缓存生成

    - 如果提供 end_user_id，只为该用户生成
    - 如果不提供，为当前工作空间的所有用户生成
    
    语言控制：
    - 使用 X-Language-Type Header 指定语言 ("zh" 中文, "en" 英文)
    - 如果未传 Header，默认使用中文 (zh)
    """
    # 使用集中化的语言校验
    language = get_language_from_header(language_type)

    workspace_id = current_user.current_workspace_id

    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试生成缓存但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    end_user_id = request.end_user_id

    api_logger.info(
        f"缓存生成请求: user={current_user.username}, workspace={workspace_id}, "
        f"end_user_id={end_user_id if end_user_id else '全部用户'}, language={language}"
    )

    try:
        if end_user_id:
            # 为单个用户生成
            api_logger.info(f"开始为单个用户生成缓存: end_user_id={end_user_id}")

            # 生成记忆洞察
            insight_result = await user_memory_service.generate_and_cache_insight(db, end_user_id, workspace_id,
                                                                                  language=language)

            # 生成用户摘要
            summary_result = await user_memory_service.generate_and_cache_summary(db, end_user_id, workspace_id,
                                                                                  language=language)

            # 构建响应
            result = {
                "end_user_id": end_user_id,
                "insight_success": insight_result["success"],
                "summary_success": summary_result["success"],
                "errors": []
            }

            # 收集错误信息
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

            # 记录结果
            if result["insight_success"] and result["summary_success"]:
                api_logger.info(f"成功为用户 {end_user_id} 生成缓存")
            else:
                api_logger.warning(f"用户 {end_user_id} 的缓存生成部分失败: {result['errors']}")

            return success(data=result, msg="生成完成")

        else:
            # 为整个工作空间生成
            api_logger.info(f"开始为工作空间 {workspace_id} 批量生成缓存")

            result = await user_memory_service.generate_cache_for_workspace(db, workspace_id, language=language)

            # 记录统计信息
            api_logger.info(
                f"工作空间 {workspace_id} 批量生成完成: "
                f"总数={result['total_users']}, 成功={result['successful']}, 失败={result['failed']}"
            )

            return success(data=result, msg="批量生成完成")

    except Exception as e:
        api_logger.error(f"缓存生成失败: user={current_user.username}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "缓存生成失败", str(e))


@router.get("/analytics/node_statistics", response_model=ApiResponse)
async def get_node_statistics_api(
        end_user_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id

    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询节点统计但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"记忆类型统计请求: end_user_id={end_user_id}, user={current_user.username}, workspace={workspace_id}")

    try:
        # 调用新的记忆类型统计函数
        result = await analytics_memory_types(db, end_user_id)

        # 计算总数用于日志
        total_count = sum(item["count"] for item in result)
        api_logger.info(
            f"成功获取记忆类型统计: end_user_id={end_user_id}, 总记忆数={total_count}, 类型数={len(result)}")
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"记忆类型查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "记忆类型查询失败", str(e))


@router.get("/analytics/graph_data", response_model=ApiResponse)
async def get_graph_data_api(
        end_user_id: str,
        node_types: Optional[str] = None,
        limit: int = 100,
        depth: int = 1,
        center_node_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id

    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询图数据但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    # 参数验证
    if limit > 1000:
        limit = 1000
        api_logger.warning("limit 参数超过最大值，已调整为 1000")

    if depth > 3:
        depth = 3
        api_logger.warning("depth 参数超过最大值，已调整为 3")

    # 解析 node_types 参数
    node_types_list = None
    if node_types:
        node_types_list = [t.strip() for t in node_types.split(",") if t.strip()]

    api_logger.info(
        f"图数据查询请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}, node_types={node_types_list}, limit={limit}, depth={depth}"
    )

    try:
        result = await analytics_graph_data(
            db=db,
            end_user_id=end_user_id,
            node_types=node_types_list,
            limit=limit,
            depth=depth,
            center_node_id=center_node_id
        )
        # 检查是否有错误消息
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


@router.get("/analytics/community_graph", response_model=ApiResponse)
async def get_community_graph_data_api(
        end_user_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
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
        result = await analytics_community_graph_data(db=db, end_user_id=end_user_id)

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

#=======================用户别名及信息接口=======================

@router.get("/user_alias", response_model=ApiResponse)
async def get_user_alias(
    user_alias_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    查询用户别名记录

    根据 user_alias_id 查询单条用户别名记录。
    """
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询用户别名但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"查询用户别名请求: user_alias_id={user_alias_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )

    result = user_memory_service.get_user_alias(db, user_alias_id)

    if result["success"]:
        api_logger.info(f"成功查询用户别名: user_alias_id={user_alias_id}")
        return success(data=result["data"], msg="查询成功")
    else:
        error_msg = result["error"]
        api_logger.error(f"查询用户别名失败: user_alias_id={user_alias_id}, error={error_msg}")
        
        if error_msg == "用户别名记录不存在":
            return fail(BizCode.USER_NOT_FOUND, "用户别名记录不存在", error_msg)
        elif error_msg == "无效的用户别名记录ID格式":
            return fail(BizCode.INVALID_USER_ID, "无效的用户别名记录ID格式", error_msg)
        else:
            return fail(BizCode.INTERNAL_ERROR, "查询用户别名失败", error_msg)


@router.post("/user_alias/create", response_model=ApiResponse)
async def create_user_alias(
    alias_create: UserAliasCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    创建用户别名记录

    为指定用户创建一条新的别名记录，支持多个别名。
    """
    workspace_id = current_user.current_workspace_id
    end_user_id = alias_create.end_user_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试创建别名但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"创建用户别名请求: end_user_id={end_user_id}, aliases={alias_create.aliases}, "
        f"user={current_user.username}, workspace={workspace_id}"
    )

    result = user_memory_service.create_user_alias(
        db, end_user_id, alias_create.other_name, alias_create.aliases, alias_create.meta_data
    )

    if result["success"]:
        api_logger.info(f"成功创建用户别名: end_user_id={end_user_id}")
        return success(data=result["data"], msg="创建成功")
    else:
        error_msg = result["error"]
        api_logger.error(f"用户别名创建失败: end_user_id={end_user_id}, error={error_msg}")
        
        if error_msg == "终端用户不存在":
            return fail(BizCode.USER_NOT_FOUND, "终端用户不存在", error_msg)
        elif error_msg == "无效的用户ID格式":
            return fail(BizCode.INVALID_USER_ID, "无效的用户ID格式", error_msg)
        else:
            return fail(BizCode.INTERNAL_ERROR, "用户别名创建失败", error_msg)


@router.post("/user_alias/updated", response_model=ApiResponse)
async def update_user_alias(
    alias_update: UserAliasUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    更新用户别名记录

    根据 user_alias_id 更新用户别名记录，支持批量更新多个别名。
    
    示例请求体：
    {
      "user_alias_id": "2d4f57d4-639b-47aa-937a-d461bc2c2d53",
      "other_name": "张三1",
      "aliases": ["小张", "张工"],
      "meta_data": {"position": "工程师", "department": "技术部"}
    }
    """
    workspace_id = current_user.current_workspace_id
    user_alias_id = alias_update.user_alias_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新用户别名但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"更新用户别名请求: user_alias_id={user_alias_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )

    # 获取更新数据（排除 user_alias_id）
    update_data = alias_update.model_dump(exclude_unset=True, exclude={'user_alias_id'})
    
    result = user_memory_service.update_user_alias(db, user_alias_id, update_data)

    if result["success"]:
        api_logger.info(f"成功更新用户别名: user_alias_id={user_alias_id}")
        return success(data=result["data"], msg="更新成功")
    else:
        error_msg = result["error"]
        api_logger.error(f"用户别名更新失败: user_alias_id={user_alias_id}, error={error_msg}")
        
        if error_msg == "用户别名记录不存在":
            return fail(BizCode.USER_NOT_FOUND, "用户别名记录不存在", error_msg)
        elif error_msg == "无效的用户别名记录ID格式":
            return fail(BizCode.INVALID_USER_ID, "无效的用户别名记录ID格式", error_msg)
        else:
            return fail(BizCode.INTERNAL_ERROR, "用户别名更新失败", error_msg)


@router.delete("/user_alias", response_model=ApiResponse)
async def delete_user_alias(
    user_alias_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    删除用户别名记录

    根据 user_alias_id 删除指定的用户别名记录。
    """
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试删除别名但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"删除用户别名请求: user_alias_id={user_alias_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )

    result = user_memory_service.delete_user_alias(db, user_alias_id)

    if result["success"]:
        api_logger.info(f"成功删除用户别名: user_alias_id={user_alias_id}")
        return success(data=result["data"], msg="删除成功")
    else:
        error_msg = result["error"]
        api_logger.error(f"用户别名删除失败: user_alias_id={user_alias_id}, error={error_msg}")
        
        if error_msg == "用户别名记录不存在":
            return fail(BizCode.USER_NOT_FOUND, "用户别名记录不存在", error_msg)
        elif error_msg == "无效的用户别名记录ID格式":
            return fail(BizCode.INVALID_USER_ID, "无效的用户别名记录ID格式", error_msg)
        else:
            return fail(BizCode.INTERNAL_ERROR, "用户别名删除失败", error_msg)

@router.get("/memory_space/timeline_memories", response_model=ApiResponse)
async def memory_space_timeline_of_shared_memories(
        id: str, label: str,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
):
    # 使用集中化的语言校验
    language = get_language_from_header(language_type)

    workspace_id = current_user.current_workspace_id
    workspace_repo = WorkspaceRepository(db)
    workspace_models = workspace_repo.get_workspace_models_configs(workspace_id)

    if workspace_models:
        model_id = workspace_models.get("llm", None)
    else:
        model_id = None
    MemoryEntity = MemoryEntityService(id, label)
    timeline_memories_result = await MemoryEntity.get_timeline_memories_server(model_id, language)

    return success(data=timeline_memories_result, msg="共同记忆时间线")


@router.get("/memory_space/relationship_evolution", response_model=ApiResponse)
async def memory_space_relationship_evolution(id: str, label: str,
                                              current_user: User = Depends(get_current_user),
                                              db: Session = Depends(get_db),
                                              ):
    try:
        api_logger.info(f"关系演变查询请求: id={id}, table={label}, user={current_user.username}")

        # 获取情绪数据
        emotion = MemoryEmotion(id, label)
        emotion_result = await emotion.get_emotion()

        # 获取交互数据
        interaction = MemoryInteraction(id, label)
        interaction_result = await interaction.get_interaction_frequency()

        # 关闭连接
        await emotion.close()
        await interaction.close()

        result = {
            "emotion": emotion_result,
            "interaction": interaction_result
        }

        api_logger.info(f"关系演变查询成功: id={id}, table={label}")
        return success(data=result, msg="关系演变")

    except Exception as e:
        api_logger.error(f"关系演变查询失败: id={id}, table={label}, error={str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "关系演变查询失败", str(e))
