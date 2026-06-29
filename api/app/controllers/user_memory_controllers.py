"""
用户记忆相关的控制器
保留终端用户信息更新与记忆空间（memory_space）相关接口。
分析类（analytics）接口已迁移至 memory_analytics_controller。
"""
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.response_utils import success, fail
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.schemas.end_user_info_schema import (
    EndUserInfoUpdate,
)
from app.schemas.memory_storage_schema import DeleteNodeRequest, DeleteAllNodesRequest
from app.schemas.response_schema import ApiResponse
from app.services.memory_entity_relationship_service import MemoryEntityService, MemoryEmotion, MemoryInteraction
from app.services.user_memory_service import UserMemoryService

# Get API logger
api_logger = get_api_logger()

# Initialize service
user_memory_service = UserMemoryService()

router = APIRouter(
    prefix="/memory-storage",
    tags=["User Memory"],
)


# =======================终端用户信息接口=======================

@router.post("/end_user_info/updated", response_model=ApiResponse)
async def update_end_user_info(
        info_update: EndUserInfoUpdate,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """
    更新终端用户信息记录

    根据 end_user_id 更新终端用户信息记录，支持批量更新多个别名。
    
    示例请求体：
    {
      "end_user_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "other_name": "张三1",
      "aliases": ["小张", "张工"],
      "meta_data": {"position": "工程师", "department": "技术部"}
    }
    """
    workspace_id = current_user.current_workspace_id
    end_user_id = info_update.end_user_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新终端用户信息但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"更新终端用户信息请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )

    # 校验 end_user 是否属于当前工作空间
    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_end_user_by_id(end_user_id)
    if end_user is None:
        return fail(BizCode.USER_NOT_FOUND, "终端用户不存在", "end_user not found")
    if str(end_user.workspace_id) != str(workspace_id):
        api_logger.warning(
            f"用户 {current_user.username} 尝试更新不属于工作空间 {workspace_id} 的终端用户 {end_user_id}"
        )
        return fail(BizCode.PERMISSION_DENIED, "该终端用户不属于当前工作空间", "end_user workspace mismatch")

    # 获取更新数据（排除 end_user_id）
    update_data = info_update.model_dump(exclude_unset=True, exclude={'end_user_id'})

    result = user_memory_service.update_end_user_info(db, end_user_id, update_data)

    if result["success"]:
        api_logger.info(f"成功更新终端用户信息: end_user_id={end_user_id}")
        return success(data=result["data"], msg="更新成功")
    else:
        error_msg = result["error"]
        api_logger.error(f"终端用户信息更新失败: end_user_id={end_user_id}, error={error_msg}")

        if error_msg == "终端用户信息记录不存在":
            return fail(BizCode.USER_NOT_FOUND, "终端用户信息记录不存在", error_msg)
        elif error_msg == "无效的终端用户ID格式":
            return fail(BizCode.INVALID_USER_ID, "无效的终端用户ID格式", error_msg)
        else:
            return fail(BizCode.INTERNAL_ERROR, "终端用户信息更新失败", error_msg)


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


@router.get("/memory_space/entity_event_timeline", response_model=ApiResponse)
async def memory_space_entity_event_timeline(
        id: str,
        label: str,
        page: int = Query(1, ge=1, description="页码，从1开始"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
        current_user: User = Depends(get_current_user),
):
    """ExtractedEntity 实体事件时间线（分页）

    Query 参数:
        id: 实体节点的 Neo4j elementId
        label: 节点类型，仅支持 ExtractedEntity
        page: 页码（从 1 开始）
        pagesize: 每页条数（1~100）

    实体基本信息（entity_name / description_summary 等）与 category_stats
    始终基于全量事件计算，分页只影响返回的 items 列表。
    """
    # 仅支持 ExtractedEntity 节点
    if label != 'ExtractedEntity':
        return fail(BizCode.INVALID_PARAMETER, "该接口仅支持 ExtractedEntity 节点", f"label={label}")

    memory_entity = MemoryEntityService(id, label)
    result = await memory_entity.get_entity_event_timeline(page=page, pagesize=pagesize)
    return success(data=result, msg="实体事件时间线")


@router.get("/memory_space/entity_timeline", response_model=ApiResponse)
async def memory_space_entity_timeline(
        id: str,
        type: str = Query("all", description="来源筛选：all/key_node/statement/memory_summary"),
        page: int = Query(1, ge=1, description="页码，从1开始"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
        current_user: User = Depends(get_current_user),
):
    """ExtractedEntity 合并记忆时间线（关键节点 / 情绪记忆 / 长期沉淀），分页 + 按来源筛选。

    本接口仅服务 ExtractedEntity 节点（只有它才有「关键节点」来源）；
    Statement / MemorySummary 节点仍走旧接口 timeline_memories。

    Query 参数:
        id: ExtractedEntity 节点的 Neo4j elementId
        type: 来源筛选，all/key_node/statement/memory_summary，默认 all
        page: 页码（从 1 开始）
        pagesize: 每页条数（1~100）

    total_count 与 type_stats 始终基于全量统计，不受 type 筛选影响。
    """
    allowed = {"all", "key_node", "statement", "memory_summary"}
    if type not in allowed:
        return fail(BizCode.INVALID_PARAMETER, "type 取值非法", f"type={type}")

    memory_entity = MemoryEntityService(id, "ExtractedEntity")
    result = await memory_entity.get_unified_timeline(
        source_type=type, page=page, pagesize=pagesize
    )
    return success(data=result, msg="记忆时间线")


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


@router.post("/node/delete", response_model=ApiResponse)
async def delete_node_api(
        request: DeleteNodeRequest,
        current_user: User = Depends(get_current_user),
) -> dict:
    """通过 elementId 删除 Neo4j 图节点（含关联边）。

    会自动校验节点归属的 end_user_id，仅删除属于指定用户的节点。
    """
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试删除节点但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    element_id = request.element_id
    end_user_id = request.end_user_id

    api_logger.info(
        f"节点删除请求: element_id={element_id}, end_user_id={end_user_id}, "
        f"user={current_user.username}, workspace={workspace_id}"
    )

    try:
        from app.core.memory.memory_service import MemoryService

        deleted = await MemoryService.delete_node_by_element_id(
            element_id=element_id,
            end_user_id=end_user_id,
        )

        if deleted:
            api_logger.info(f"节点删除成功: element_id={element_id}, end_user_id={end_user_id}")

            # 同步 memory_count 到 PostgreSQL
            try:
                from app.core.memory.utils.memory_count_utils import sync_end_user_memory_count_from_neo4j
                from app.repositories.neo4j.neo4j_connector import Neo4jConnector
                async with Neo4jConnector() as sync_connector:
                    await sync_end_user_memory_count_from_neo4j(end_user_id, sync_connector)
            except Exception as sync_err:
                api_logger.warning(f"同步 memory_count 失败（不影响删除结果）: {sync_err}")

            return success(data={"deleted": True, "element_id": element_id}, msg="节点删除成功")
        else:
            api_logger.warning(f"节点未找到或不属于该用户: element_id={element_id}, end_user_id={end_user_id}")
            return fail(BizCode.NOT_FOUND, "节点未找到或不属于该用户", f"element_id={element_id}")

    except Exception as e:
        api_logger.error(f"节点删除失败: element_id={element_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "节点删除失败", str(e))


@router.post("/nodes/delete-all", response_model=ApiResponse)
async def delete_all_nodes_api(
        request: DeleteAllNodesRequest,
        current_user: User = Depends(get_current_user),
) -> dict:
    """删除指定用户的所有 Neo4j 记忆节点和边。

    分批 DETACH DELETE + 清理残留边，完成后同步 memory_count 到 PostgreSQL。
    这是一个危险操作，会永久删除该用户的所有记忆图数据。
    """
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试删除所有节点但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    end_user_id = request.end_user_id

    api_logger.info(
        f"批量删除节点请求: end_user_id={end_user_id}, "
        f"user={current_user.username}, workspace={workspace_id}"
    )

    try:
        from app.core.memory.memory_service import MemoryService

        total_deleted = await MemoryService.delete_all_nodes_by_end_user_id(end_user_id)

        try:
            from app.repositories.end_user_repository import EndUserRepository
            from app.db import get_db_context
            from uuid import UUID
            with get_db_context() as db:
                EndUserRepository(db).update_memory_count(UUID(end_user_id), 0)
        except Exception as sync_err:
            api_logger.warning(f"同步 memory_count 失败（不影响删除结果）: {sync_err}")

        api_logger.info(f"批量删除完成: end_user_id={end_user_id}, total_deleted={total_deleted}")
        return success(
            data={"deleted": True, "end_user_id": end_user_id, "total_deleted": total_deleted},
            msg=f"成功删除 {total_deleted} 个节点"
        )

    except Exception as e:
        api_logger.error(f"批量删除节点失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "批量删除节点失败", str(e))
