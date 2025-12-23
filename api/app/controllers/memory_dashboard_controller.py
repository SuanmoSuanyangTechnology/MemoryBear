from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import uuid
from app.repositories.end_user_repository import update_end_user_other_name
import uuid
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.memory_agent_schema import End_User_Information
from app.schemas.response_schema import ApiResponse
from app.schemas.app_schema import App as AppSchema

from app.services import memory_dashboard_service, memory_storage_service, workspace_service
from app.core.logging_config import get_api_logger

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
    dependencies=[Depends(get_current_user)] # Apply auth to all routes in this controller
)


@router.get("/total_end_users", response_model=ApiResponse)
def get_workspace_total_end_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取用户列表的总用户数
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的宿主列表")
    total_end_users = memory_dashboard_service.get_workspace_total_end_users(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user
    )
    api_logger.info(f"成功获取最新用户总数: total_num={total_end_users.get('total_num', 0)}")
    return success(data=total_end_users, msg="用户数量获取成功")

@router.post("/update/end_users", response_model=ApiResponse)
async def update_workspace_end_users(
    user_input: End_User_Information,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    更新工作空间的宿主信息
    """
    username = user_input.end_user_name  # 要更新的用户名
    end_user_input_id = user_input.id  # 宿主ID
    workspace_id = current_user.current_workspace_id
    
    api_logger.info(f"用户 {current_user.username} 请求更新工作空间 {workspace_id} 的宿主信息")
    api_logger.info(f"更新参数: username={username}, end_user_id={end_user_input_id}")

    try:
        # 导入更新函数
        from app.repositories.end_user_repository import update_end_user_other_name
        import uuid
        
        # 转换 end_user_id 为 UUID 类型
        end_user_uuid = uuid.UUID(end_user_input_id)
        
        # 直接更新数据库中的 other_name 字段
        updated_count = update_end_user_other_name(
            db=db,
            end_user_id=end_user_uuid,
            other_name=username
        )
        
        api_logger.info(f"成功更新宿主 {end_user_input_id} 的 other_name 为: {username}")

        return success(
            data={
                "updated_count": updated_count,
                "end_user_id": end_user_input_id,
                "updated_other_name": username
            },
            msg=f"成功更新 {updated_count} 个宿主的信息"
        )
        
    except Exception as e:
        api_logger.error(f"更新宿主信息失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新宿主信息失败: {str(e)}"
        )



@router.get("/end_users", response_model=ApiResponse)
async def get_workspace_end_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取工作空间的宿主列表
    
    返回格式与原 memory_list 接口中的 end_users 字段相同
    """
    workspace_id = current_user.current_workspace_id
    # 获取当前空间类型
    current_workspace_type = memory_dashboard_service.get_current_workspace_type(db, workspace_id, current_user)
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的宿主列表")
    end_users = memory_dashboard_service.get_workspace_end_users(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user
    )
    result = []
    for end_user in end_users:
        memory_num = {}
        if current_workspace_type == "neo4j":
            # EndUser 是 Pydantic 模型，直接访问属性而不是使用 .get()
            memory_num = await memory_storage_service.search_all(str(end_user.id))
        elif current_workspace_type == "rag":
            memory_num = {
                "total":memory_dashboard_service.get_current_user_total_chunk(str(end_user.id), db, current_user)
            }
        result.append(
            {
                'end_user':end_user,
                'memory_num':memory_num
            }
        )
        
    api_logger.info(f"成功获取 {len(end_users)} 个宿主记录")
    return success(data=result, msg="宿主列表获取成功")


@router.get("/memory_increment", response_model=ApiResponse)
def get_workspace_memory_increment(
    limit: int = Query(7, description="返回记录数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取工作空间的记忆增量"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆增量")
    memory_increment = memory_dashboard_service.get_workspace_memory_increment(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user,
        limit=limit
    )
    api_logger.info(f"成功获取 {len(memory_increment)} 条记忆增量记录")
    return success(data=memory_increment, msg="记忆增量获取成功")


@router.get("/api_increment", response_model=ApiResponse)
def get_workspace_api_increment(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取API调用趋势"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的API调用增量")
    api_increment = memory_dashboard_service.get_workspace_api_increment(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user
    )
    api_logger.info(f"成功获取 {api_increment} API调用增量")
    return success(data=api_increment, msg="API调用增量获取成功")


@router.post("/total_memory", response_model=ApiResponse)
def write_workspace_total_memory(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """工作空间记忆总量的写入（异步任务）"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求写入工作空间 {workspace_id} 的记忆总量")
    
    # 触发 Celery 异步任务
    from app.celery_app import celery_app
    task = celery_app.send_task(
        "app.controllers.memory_storage_controller.search_all",
        kwargs={"workspace_id": str(workspace_id)}
    )
    
    api_logger.info(f"已触发记忆总量统计任务，task_id: {task.id}")
    return success(
        data={"task_id": task.id, "workspace_id": str(workspace_id)},
        msg="记忆总量统计任务已启动"
    )


@router.get("/task_status/{task_id}", response_model=ApiResponse)
def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """查询异步任务的执行状态和结果"""
    api_logger.info(f"用户 {current_user.username} 查询任务状态: task_id={task_id}")
    
    from app.celery_app import celery_app
    from celery.result import AsyncResult
    
    # 获取任务结果
    task_result = AsyncResult(task_id, app=celery_app)
    
    response_data = {
        "task_id": task_id,
        "status": task_result.state,  # PENDING, STARTED, SUCCESS, FAILURE, RETRY, REVOKED
    }
    
    # 如果任务完成，返回结果
    if task_result.ready():
        if task_result.successful():
            response_data["result"] = task_result.result
            api_logger.info(f"任务 {task_id} 执行成功")
            return success(data=response_data, msg="任务执行成功")
        else:
            # 任务失败
            response_data["error"] = str(task_result.result)
            api_logger.error(f"任务 {task_id} 执行失败: {task_result.result}")
            return success(data=response_data, msg="任务执行失败")
    else:
        # 任务还在执行中
        api_logger.info(f"任务 {task_id} 状态: {task_result.state}")
        return success(data=response_data, msg=f"任务状态: {task_result.state}")


@router.get("/memory_list", response_model=ApiResponse)
def get_workspace_memory_list(
    limit: int = Query(7, description="记忆增量返回记录数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    用户记忆列表整合接口
    
    整合以下三个接口的数据：
    1. total_memory - 工作空间记忆总量
    2. memory_increment - 工作空间记忆增量
    3. hosts - 工作空间宿主列表
    
    返回格式：
    {
        "total_memory": float,
        "memory_increment": [
            {"date": "2024-01-01", "count": 100},
            ...
        ],
        "hosts": [
            {"id": "uuid", "name": "宿主名", ...},
            ...
        ]
    }
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆列表")
    memory_list = memory_dashboard_service.get_workspace_memory_list(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user,
        limit=limit
    )
    api_logger.info("成功获取记忆列表")
    return success(data=memory_list, msg="记忆列表获取成功")


@router.get("/total_memory_count", response_model=ApiResponse)
async def get_workspace_total_memory_count(
    end_user_id: Optional[str] = Query(None, description="可选的用户ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取工作空间的记忆总量（通过聚合所有host的记忆数）
    
    逻辑：
    1. 从 memory_list 获取所有 host_id
    2. 对每个 host_id 调用 search_all 获取 total
    3. 将所有 total 求和返回
    
    返回格式：
    {
        "total_memory_count": int,
        "host_count": int,
        "details": [
            {"end_user_id": "uuid", "count": 100, "name": "用户名称"},
            ...
        ]
    }
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆总量")
    total_memory_count = await memory_dashboard_service.get_workspace_total_memory_count(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user,
        end_user_id=end_user_id
    )
    api_logger.info(f"成功获取记忆总量: {total_memory_count.get('total_memory_count', 0)}")
    return success(data=total_memory_count, msg="记忆总量获取成功")


# ======== RAG 数据统计 ========
@router.get("/total_rag_count", response_model=ApiResponse)
def get_workspace_total_rag_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取 rag 的总文档数、总chunk数、总知识库数量、总api调用数量
    """
    total_documents = memory_dashboard_service.get_rag_total_doc(db, current_user)
    total_chunk = memory_dashboard_service.get_rag_total_chunk(db, current_user)
    total_kb = memory_dashboard_service.get_rag_total_kb(db, current_user)
    data = {
        'total_documents':total_documents,
        'total_chunk':total_chunk,
        'total_kb':total_kb,
        'total_api':1024
    }
    return success(data=data, msg="RAG相关数据获取成功")

@router.get("/current_user_rag_total_num", response_model=ApiResponse)
def get_current_user_rag_total_num(
    end_user_id: str = Query(..., description="宿主ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取当前宿主的 RAG 的总chunk数量
    """
    total_chunk = memory_dashboard_service.get_current_user_total_chunk(end_user_id, db, current_user)
    return success(data=total_chunk, msg="宿主RAG知识数据获取成功")

@router.get("/rag_content", response_model=ApiResponse)
def get_rag_content(
    end_user_id: str = Query(..., description="宿主ID"),
    limit: int = Query(15, description="返回记录数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取当前宿主知识库中的chunk内容
    """
    data = memory_dashboard_service.get_rag_content(end_user_id, limit, db, current_user)
    return success(data=data, msg="宿主RAGchunk数据获取成功")


@router.get("/chunk_summary_tag", response_model=ApiResponse)
async def get_chunk_summary_tag(
    end_user_id: str = Query(..., description="宿主ID"),
    limit: int = Query(15, description="返回记录数"),
    max_tags: int = Query(10, description="最大标签数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取chunk总结、提取的标签和人物形象
    
    返回格式：
    {
        "summary": "chunk内容的总结",
        "tags": [
            {"tag": "标签1", "frequency": 5},
            {"tag": "标签2", "frequency": 3},
            ...
        ],
        "personas": [
            "产品设计师",
            "旅行爱好者",
            "摄影发烧友",
            ...
        ]
    }
    """
    api_logger.info(f"用户 {current_user.username} 请求获取宿主 {end_user_id} 的chunk摘要、标签和人物形象")
    
    data = await memory_dashboard_service.get_chunk_summary_and_tags(
        end_user_id=end_user_id,
        limit=limit,
        max_tags=max_tags,
        db=db,
        current_user=current_user
    )
    
    api_logger.info(f"成功获取chunk摘要、{len(data.get('tags', []))} 个标签和 {len(data.get('personas', []))} 个人物形象")
    return success(data=data, msg="chunk摘要、标签和人物形象获取成功")


@router.get("/chunk_insight", response_model=ApiResponse)
async def get_chunk_insight(
    end_user_id: str = Query(..., description="宿主ID"),
    limit: int = Query(15, description="返回记录数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取chunk的洞察内容
    
    返回格式：
    {
        "insight": "对chunk内容的深度洞察分析"
    }
    """
    api_logger.info(f"用户 {current_user.username} 请求获取宿主 {end_user_id} 的chunk洞察")
    
    data = await memory_dashboard_service.get_chunk_insight(
        end_user_id=end_user_id,
        limit=limit,
        db=db,
        current_user=current_user
    )
    
    api_logger.info("成功获取chunk洞察")
    return success(data=data, msg="chunk洞察获取成功")


@router.get("/dashboard_data", response_model=ApiResponse)
async def dashboard_data(
    end_user_id: Optional[str] = Query(None, description="可选的用户ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    整合dashboard数据接口
    
    整合以下接口的数据：
    1. /dashboard/total_memory_count - 记忆总量
    2. /dashboard/api_increment - API调用增量
    3. /memory/stats/types - 知识库类型统计（只要total数据）
    4. /dashboard/total_rag_count - RAG相关数据
    
    根据 storage_type 判断调用不同的接口
    
    返回格式：
    {
        "storage_type": str,
        "neo4j_data": {
            "total_memory": int,
            "total_app": int,
            "total_knowledge": int,
            "total_api_call": int
        } | null,
        "rag_data": {
            "total_memory": int,
            "total_app": int,
            "total_knowledge": int,
            "total_api_call": int
        } | null
    }
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的dashboard整合数据")
    
    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None:
        storage_type = 'neo4j'
    
    user_rag_memory_id = None
    
    # 根据 storage_type 决定返回哪个数据对象
    # 如果是 'rag'，neo4j_data 为 null；否则 rag_data 为 null
    result = {
        "storage_type": storage_type,
        "neo4j_data": None,
        "rag_data": None
    }
    
    try:
        # 如果 storage_type 为 'neo4j' 或空，获取 neo4j_data
        if storage_type == 'neo4j':
            neo4j_data = {
                "total_memory": None,
                "total_app": None,
                "total_knowledge": None,
                "total_api_call": None
            }
            
            # 1. 获取记忆总量（total_memory）
            try:
                total_memory_data = await memory_dashboard_service.get_workspace_total_memory_count(
                    db=db,
                    workspace_id=workspace_id,
                    current_user=current_user,
                    end_user_id=end_user_id
                )
                neo4j_data["total_memory"] = total_memory_data.get("total_memory_count", 0)
                # total_app: 统计当前空间下的所有app数量
                from app.repositories import app_repository
                apps_orm = app_repository.get_apps_by_workspace_id(db, workspace_id)
                neo4j_data["total_app"] = len(apps_orm)
                api_logger.info(f"成功获取记忆总量: {neo4j_data['total_memory']}, 应用数量: {neo4j_data['total_app']}")
            except Exception as e:
                api_logger.warning(f"获取记忆总量失败: {str(e)}")
            
            # 2. 获取知识库类型统计（total_knowledge）
            try:
                from app.services.memory_agent_service import MemoryAgentService 
                memory_agent_service = MemoryAgentService()
                knowledge_stats = await memory_agent_service.get_knowledge_type_stats(
                    end_user_id=end_user_id,
                    only_active=True,
                    current_workspace_id=workspace_id,
                    db=db
                )
                neo4j_data["total_knowledge"] = knowledge_stats.get("total", 0)
                api_logger.info(f"成功获取知识库类型统计total: {neo4j_data['total_knowledge']}")
            except Exception as e:
                api_logger.warning(f"获取知识库类型统计失败: {str(e)}")
            
            # 3. 获取API调用增量（total_api_call，转换为整数）
            try:
                api_increment = memory_dashboard_service.get_workspace_api_increment(
                    db=db,
                    workspace_id=workspace_id,
                    current_user=current_user
                )
                neo4j_data["total_api_call"] = api_increment
                api_logger.info(f"成功获取API调用增量: {neo4j_data['total_api_call']}")
            except Exception as e:
                api_logger.warning(f"获取API调用增量失败: {str(e)}")
            
            result["neo4j_data"] = neo4j_data
            api_logger.info("成功获取neo4j_data")
        
        # 如果 storage_type 为 'rag'，获取 rag_data
        elif storage_type == 'rag':
            rag_data = {
                "total_memory": None,
                "total_app": None,
                "total_knowledge": None,
                "total_api_call": None
            }
            
            # 获取RAG相关数据
            try:
                # total_memory: 使用 total_chunk（总chunk数）
                total_chunk = memory_dashboard_service.get_rag_total_chunk(db, current_user)
                rag_data["total_memory"] = total_chunk
                
                # total_app: 统计当前空间下的所有app数量
                from app.repositories import app_repository
                apps_orm = app_repository.get_apps_by_workspace_id(db, workspace_id)
                rag_data["total_app"] = len(apps_orm)
                
                # total_knowledge: 使用 total_kb（总知识库数）
                total_kb = memory_dashboard_service.get_rag_total_kb(db, current_user)
                rag_data["total_knowledge"] = total_kb
                
                # total_api_call: 固定值
                rag_data["total_api_call"] = 1024
                
                api_logger.info(f"成功获取RAG相关数据: memory={total_chunk}, app={len(apps_orm)}, knowledge={total_kb}")
            except Exception as e:
                api_logger.warning(f"获取RAG相关数据失败: {str(e)}")
            
            result["rag_data"] = rag_data
            api_logger.info("成功获取rag_data")
        
        api_logger.info("成功获取dashboard整合数据")
        return success(data=result, msg="Dashboard数据获取成功")
        
    except Exception as e:
        api_logger.error(f"获取dashboard整合数据失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取dashboard整合数据失败: {str(e)}"
        )