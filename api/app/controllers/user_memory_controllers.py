"""
用户记忆相关的控制器
保留记忆空间（memory_space）相关接口。
分析类（analytics）接口已迁移至 memory_analytics_controller；
终端用户信息接口已迁移至 end_user_controller。
"""
from fastapi import APIRouter, Depends, Header, Query

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot, get_current_user_async
from app.repositories.workspace_repository import WorkspaceRepository
from app.schemas.memory_storage_schema import DeleteNodeRequest
from app.schemas.response_schema import ApiResponse
from app.services.memory_entity_relationship_service import MemoryEmotion, MemoryEntityService, MemoryInteraction
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
# update_end_user_info 已迁移至 end_user_controller（/end_user/info/update）


@router.get("/memory_space/timeline_memories", response_model=ApiResponse) # NOTE（乐力齐）确定这个接口还在被使用
async def memory_space_timeline_of_shared_memories(
        id: str, label: str,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """共同记忆时间线（异步版本）。"""
    language = get_language_from_header(language_type)

    workspace_id = current_user.current_workspace_id

    async with get_async_db_context() as db:
        workspace_repo = WorkspaceRepository(db)
        workspace_models = await workspace_repo.get_workspace_models_configs_async(workspace_id)

    model_id = workspace_models.get("llm") if workspace_models else None

    memory_entity = MemoryEntityService(id, label)
    timeline_memories_result = await memory_entity.get_timeline_memories_server(model_id, language)

    return success(data=timeline_memories_result, msg="共同记忆时间线")


@router.get("/memory_space/entity_event_timeline", response_model=ApiResponse)
async def memory_space_entity_event_timeline(
        id: str,
        label: str,
        page: int = Query(1, ge=1, description="页码，从1开始"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
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
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
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
async def memory_space_relationship_evolution(
        id: str,
        label: str,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """关系演变查询（异步版本）"""
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
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
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
            operator=current_user.id,
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

