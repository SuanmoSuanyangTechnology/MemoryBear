from typing import List, Optional

from app.celery_app import celery_app
from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.rag.llm.cv_model import QWenCV
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import cur_workspace_access_guard, get_current_user
from app.models import ModelApiKey
from app.models.user_model import User
from app.repositories import knowledge_repository
from app.schemas.memory_agent_schema import UserInput, Write_UserInput
from app.schemas.response_schema import ApiResponse
from app.services import task_service, workspace_service
from app.services.memory_agent_service import MemoryAgentService
from app.services.model_service import ModelConfigService
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

load_dotenv()
api_logger = get_api_logger()

memory_agent_service = MemoryAgentService()

router = APIRouter(
    prefix="/memory",
    tags=["Memory"],
)


@router.get("/health/status", response_model=ApiResponse)
async def get_health_status(
    current_user: User = Depends(get_current_user)
):
    """
    Get latest health status written by Celery periodic task
    
    Returns health status information from Redis cache
    """
    api_logger.info("Health status check requested")
    try:
        result = await memory_agent_service.get_health_status()
        return success(data=result["status"])
    except Exception as e:
        api_logger.error(f"Health status check failed: {str(e)}")
        return fail(BizCode.SERVICE_UNAVAILABLE, "健康状态查询失败", str(e))


@router.get("/download_log")
async def download_log(
    log_type: str = Query("file", regex="^(file|transmission)$", description="日志类型: file=完整文件, transmission=实时流式传输"),
    current_user: User = Depends(get_current_user)
):
    """
    Download or stream agent service log file
    
        log_type: str = Query("file", regex="^(file|transmission)$",
                              description="日志类型: file=完整文件, transmission=实时流式传输"),
        current_user: User = Depends(get_current_user)

    Args:
        log_type: Log retrieval mode
            - "file": Returns complete log file content in single response (default)
            - "transmission": Real-time streaming of log content using Server-Sent Events
    
    Returns:
        - file mode: ApiResponse with log content
        - transmission mode: StreamingResponse with SSE
    """
    api_logger.info(f"Log download requested with log_type={log_type}")
    
    # Validate log_type parameter (FastAPI Query regex already validates, but explicit check for clarity)
    if log_type not in ["file", "transmission"]:
        api_logger.warning(f"Invalid log_type parameter: {log_type}")
        return fail(
            BizCode.BAD_REQUEST, 
            "无效的log_type参数", 
            "log_type必须是'file'或'transmission'"
        )
    
    # Route to appropriate mode
    if log_type == "file":
        # File mode: Return complete log file content
        try:
            log_content = memory_agent_service.get_log_content()
            return success(data=log_content)
        except ValueError as e:
            api_logger.warning(f"Log content issue: {str(e)}")
            return fail(BizCode.FILE_NOT_FOUND, str(e))
        except Exception as e:
            api_logger.error(f"Log reading failed: {str(e)}")
            return fail(BizCode.FILE_READ_ERROR, "日志读取失败", str(e))

    else:  # log_type == "transmission"
        # Transmission mode: Stream log content using SSE
        try:
            api_logger.info("Starting SSE log streaming")
            return StreamingResponse(
                memory_agent_service.stream_log_content(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"  # Disable nginx buffering
                }
            )
        except Exception as e:
            api_logger.error(f"Failed to start log streaming: {str(e)}")
            return fail(BizCode.INTERNAL_ERROR, "启动日志流式传输失败", str(e))


@router.post("/writer_service", response_model=ApiResponse)
@cur_workspace_access_guard()
async def write_server(
    user_input: Write_UserInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Write service endpoint - processes write operations synchronously
    
    Args:
        user_input: Write request containing message and group_id
    
    Returns:
        Response with write operation status
    """
    config_id = user_input.config_id
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"Write service: workspace_id={workspace_id}, config_id={config_id}")
    
    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None: storage_type = 'neo4j'
    user_rag_memory_id = ''
    
    # 如果 storage_type 是 rag，必须确保有有效的 user_rag_memory_id
    if storage_type == 'rag':
        if workspace_id:
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=db,
                name="USER_RAG_MERORY",
                workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)
            else:
                api_logger.warning(f"未找到名为 'USER_RAG_MERORY' 的知识库，workspace_id: {workspace_id}，将使用 neo4j 存储")
                storage_type = 'neo4j'
        else:
            api_logger.warning("workspace_id 为空，无法使用 rag 存储，将使用 neo4j 存储")
            storage_type = 'neo4j'
    
    api_logger.info(f"Write service requested for group {user_input.group_id}, storage_type: {storage_type}, user_rag_memory_id: {user_rag_memory_id}")
    try:
        result = await memory_agent_service.write_memory(
            user_input.group_id, 
            user_input.message, 
            config_id,
            db,
            storage_type, 
            user_rag_memory_id
        )
        return success(data=result, msg="写入成功")
    except BaseException as e:
        # Handle ExceptionGroup from TaskGroup (Python 3.11+) or BaseExceptionGroup
        if hasattr(e, 'exceptions'):
            error_messages = [f"{type(sub_e).__name__}: {str(sub_e)}" for sub_e in e.exceptions]
            detailed_error = "; ".join(error_messages)
            api_logger.error(f"Write operation error (TaskGroup): {detailed_error}", exc_info=True)
            return fail(BizCode.INTERNAL_ERROR, "写入失败", detailed_error)
        api_logger.error(f"Write operation error: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "写入失败", str(e))


@router.post("/writer_service_async", response_model=ApiResponse)
@cur_workspace_access_guard()
async def write_server_async(
    user_input: Write_UserInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Async write service endpoint - enqueues write processing to Celery
    
    Args:
        user_input: Write request containing message and group_id
    
    Returns:
        Task ID for tracking async operation
        Use GET /memory/write_result/{task_id} to check task status and get result
    """
    config_id = user_input.config_id
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"Async write service: workspace_id={workspace_id}, config_id={config_id}")

    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None: storage_type = 'neo4j'
    user_rag_memory_id = ''
    if workspace_id:

        knowledge = knowledge_repository.get_knowledge_by_name(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id
        )
        if knowledge: user_rag_memory_id = str(knowledge.id)
    api_logger.info(f"Async write: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")
    try:
        task = celery_app.send_task(
            "app.core.memory.agent.write_message",
            args=[user_input.group_id, user_input.message, config_id, storage_type, user_rag_memory_id]
        )
        api_logger.info(f"Write task queued: {task.id}")
        
        return success(data={"task_id": task.id}, msg="写入任务已提交")
    except Exception as e:
        api_logger.error(f"Async write operation failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "写入失败", str(e))


@router.post("/read_service", response_model=ApiResponse)
@cur_workspace_access_guard()
async def read_server(
    user_input: UserInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Read service endpoint - processes read operations synchronously
    
    search_switch values:
    - "0": Requires verification
    - "1": No verification, direct split
    - "2": Direct answer based on context
    
    Args:
        user_input: Read request with message, history, search_switch, and group_id
    
    Returns:
        Response with query answer
    """
    config_id = user_input.config_id
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"Read service: workspace_id={workspace_id}, config_id={config_id}")

    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None: storage_type = 'neo4j'
    user_rag_memory_id = ''
    if workspace_id:

        knowledge = knowledge_repository.get_knowledge_by_name(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id
        )
        if knowledge: user_rag_memory_id = str(knowledge.id)
    
    api_logger.info(f"Read service: group={user_input.group_id}, storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}, workspace_id={workspace_id}")
    try:
        result = await memory_agent_service.read_memory(
            user_input.group_id,
            user_input.message,
            user_input.history,
            user_input.search_switch,
            config_id,
            db,
            storage_type,
            user_rag_memory_id
        )
        return success(data=result, msg="回复对话消息成功")
    except BaseException as e:
        # Handle ExceptionGroup from TaskGroup (Python 3.11+) or BaseExceptionGroup
        if hasattr(e, 'exceptions'):
            error_messages = [f"{type(sub_e).__name__}: {str(sub_e)}" for sub_e in e.exceptions]
            detailed_error = "; ".join(error_messages)
            api_logger.error(f"Read operation error (TaskGroup): {detailed_error}", exc_info=True)
            return fail(BizCode.INTERNAL_ERROR, "回复对话消息失败", detailed_error)
        api_logger.error(f"Read operation error: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "回复对话消息失败", str(e))


@router.post("/file", response_model=ApiResponse)
async def file_update(
        files: List[UploadFile] = File(..., description="要上传的文件"),
        model_id:str = Form(..., description="模型ID"),
        metadata: Optional[str] = Form(None, description="文件元数据 (JSON格式)"),
        current_user: User = Depends(get_current_user)
):
    """
    文件上传接口 - 支持图片识别
    
    Args:
        files: 上传的文件列表
        metadata: 文件元数据（可选）
        current_user: 当前用户
    
    Returns:
        文件处理结果
    """

    db_gen = get_db()             # get_db 通常是一个生成器
    db = next(db_gen)
    api_logger.info(f"File upload requested, file count: {len(files)}")
    config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
    apiConfig: ModelApiKey = config.api_keys[0]
    file_content = []
    try:
        for file in files:
            api_logger.debug(f"Processing file: {file.filename}, content_type: {file.content_type}")
            content = await file.read()
            
            if file.content_type and file.content_type.startswith("image/"):
                vision_model = QWenCV(
                    key=apiConfig.api_key,
                    model_name=apiConfig.model_name,
                    lang="Chinese",
                    base_url=apiConfig.api_base
                )
                description, token_count = vision_model.describe(content)
                file_content.append(description)
                api_logger.info(f"Image processed: {file.filename}, tokens: {token_count}")
            else:
                api_logger.warning(f"Unsupported file type: {file.content_type}")
                file_content.append(f"[不支持的文件类型: {file.content_type}]")
        
        result_text = ';'.join(file_content)
        api_logger.info(f"File processing completed, result length: {len(result_text)}")
        
        return success(data=result_text, msg="转换文本成功")
        
    except Exception as e:
        api_logger.error(f"File processing failed: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "转换文本失败", str(e))


@router.post("/read_service_async", response_model=ApiResponse)
@cur_workspace_access_guard()
async def read_server_async(
        user_input: UserInput,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    config_id = user_input.config_id
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"Async read service: workspace_id={workspace_id}, config_id={config_id}")

    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None: storage_type = 'neo4j'
    user_rag_memory_id = ''
    if workspace_id:

        knowledge = knowledge_repository.get_knowledge_by_name(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id
        )
        if knowledge: user_rag_memory_id = str(knowledge.id)
    api_logger.info(f"Async read: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")
    try:
        task = celery_app.send_task(
            "app.core.memory.agent.read_message",
            args=[user_input.group_id, user_input.message, user_input.history, user_input.search_switch,
                  config_id, storage_type, user_rag_memory_id]
        )
        api_logger.info(f"Read task queued: {task.id}")

        return success(data={"task_id": task.id}, msg="查询任务已提交")
    except Exception as e:
        api_logger.error(f"Async read operation failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "回复对话消息失败", str(e))


@router.get("/read_result/", response_model=ApiResponse)
async def get_read_task_result(
    task_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Get the status and result of an async read task
    
    Args:
        task_id: Celery task ID returned from /read_service_async
    
    Returns:
        Task status and result if completed
        
    Response format:
        - PENDING: Task is waiting to be executed
        - STARTED: Task has started
        - SUCCESS: Task completed successfully, returns result
        - FAILURE: Task failed, returns error message
    """
    api_logger.info(f"Read task status check requested for task {task_id}")
    try:
        result = task_service.get_task_memory_read_result(task_id)
        status = result.get("status")
        
        if status == "SUCCESS":
            # 任务成功完成
            task_result = result.get("result", {})
            if isinstance(task_result, dict):
                # 新格式：包含详细信息
                return success(
                    data={
                        "result": task_result.get("result"),
                        "group_id": task_result.get("group_id"),
                        "elapsed_time": task_result.get("elapsed_time"),
                        "task_id": task_id
                    },
                    msg="查询任务已完成"
                )
            else:
                # 旧格式：直接返回结果
                return success(data=task_result, msg="查询任务已完成")
                
        elif status == "FAILURE":
            # 任务失败
            error_info = result.get("result", "Unknown error")
            if isinstance(error_info, dict):
                error_msg = error_info.get("error", str(error_info))
            else:
                error_msg = str(error_info)
            return fail(BizCode.INTERNAL_ERROR, "查询任务失败", error_msg)
            
        elif status in ["PENDING", "STARTED"]:
            # 任务进行中
            return success(
                data={
                    "status": status,
                    "task_id": task_id,
                    "message": "任务处理中，请稍后查询"
                },
                msg="查询任务处理中"
            )
        else:
            # 未知状态
            return success(
                data={
                    "status": status,
                    "task_id": task_id
                },
                msg=f"任务状态: {status}"
            )
            
    except Exception as e:
        api_logger.error(f"Read task status check failed: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "任务状态查询失败", str(e))


@router.get("/write_result/", response_model=ApiResponse)
async def get_write_task_result(
    task_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Get the status and result of an async write task
    
    Args:
        task_id: Celery task ID returned from /writer_service_async
    
    Returns:
        Task status and result if completed
        
    Response format:
        - PENDING: Task is waiting to be executed
        - STARTED: Task has started
        - SUCCESS: Task completed successfully, returns result
        - FAILURE: Task failed, returns error message
    """
    api_logger.info(f"Write task status check requested for task {task_id}")
    try:
        result = task_service.get_task_memory_write_result(task_id)
        status = result.get("status")
        
        if status == "SUCCESS":
            # 任务成功完成
            task_result = result.get("result", {})
            if isinstance(task_result, dict):
                # 新格式：包含详细信息
                return success(
                    data={
                        "result": task_result.get("result"),
                        "group_id": task_result.get("group_id"),
                        "elapsed_time": task_result.get("elapsed_time"),
                        "task_id": task_id
                    },
                    msg="写入任务已完成"
                )
            else:
                # 旧格式：直接返回结果
                return success(data=task_result, msg="写入任务已完成")
                
        elif status == "FAILURE":
            # 任务失败
            error_info = result.get("result", "Unknown error")
            if isinstance(error_info, dict):
                error_msg = error_info.get("error", str(error_info))
            else:
                error_msg = str(error_info)
            return fail(BizCode.INTERNAL_ERROR, "写入任务失败", error_msg)
            
        elif status in ["PENDING", "STARTED"]:
            # 任务进行中
            return success(
                data={
                    "status": status,
                    "task_id": task_id,
                    "message": "任务处理中，请稍后查询"
                },
                msg="写入任务处理中"
            )
        else:
            # 未知状态
            return success(
                data={
                    "status": status,
                    "task_id": task_id
                },
                msg=f"任务状态: {status}"
            )
            
    except Exception as e:
        api_logger.error(f"Write task status check failed: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "任务状态查询失败", str(e))


@router.post("/status_type", response_model=ApiResponse)
async def status_type(
    user_input: Write_UserInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Determine the type of user message (read or write)
    
    Args:
        user_input: Request containing user message and group_id
    
    Returns:
        Type classification result
    """
    api_logger.info(f"Status type check requested for group {user_input.group_id}")
    try:
        result = await memory_agent_service.classify_message_type(
            user_input.message,
            user_input.config_id,
            db
        )
        return success(data=result)
    except Exception as e:
        api_logger.error(f"Message type classification failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "类型判断失败", str(e))


# ==================== 新增的三个接口路由 ====================

@router.get("/stats/types", response_model=ApiResponse)
async def get_knowledge_type_stats_api(
    end_user_id: Optional[str] = Query(None, description="用户ID（可选）"),
    only_active: bool = Query(True, description="仅统计有效记录(status=1)"),
    current_user: User = Depends(get_current_user)
):
    """
    统计当前空间下各知识库类型的数量，包含 General | Web | Third-party | Folder | memory。
    会对缺失类型补 0，返回字典形式。
    可选按状态过滤。
    - 知识库类型根据当前用户的 current_workspace_id 过滤
    - memory 是 Neo4j 中 Chunk 的数量，根据 end_user_id (group_id) 过滤
    - 如果用户没有当前工作空间或未提供 end_user_id，对应的统计返回 0
    """
    api_logger.info(f"Knowledge type stats requested for workspace_id: {current_user.current_workspace_id}, end_user_id: {end_user_id}")
    try:
        from app.db import get_db
        
        # 获取数据库会话
        db_gen = get_db()
        db = next(db_gen)
        
        # 调用service层函数
        result = await memory_agent_service.get_knowledge_type_stats(
            end_user_id=end_user_id,
            only_active=only_active,
            current_workspace_id=current_user.current_workspace_id,
            db=db
        )
        
        return success(data=result, msg="获取知识库类型统计成功")
    except Exception as e:
        api_logger.error(f"Knowledge type stats failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取知识库类型统计失败", str(e))


@router.get("/analytics/hot_memory_tags/by_user", response_model=ApiResponse)
async def get_hot_memory_tags_by_user_api(
    end_user_id: Optional[str] = Query(None, description="用户ID（可选）"),
    limit: int = Query(20, description="返回标签数量限制"),
    current_user: User = Depends(get_current_user)
):
    """
    获取指定用户的热门记忆标签
    
    返回格式：
    [
        {"name": "标签名", "frequency": 频次},
        ...
    ]
    """
    api_logger.info(f"Hot memory tags by user requested: end_user_id={end_user_id}")
    try:
        result = await memory_agent_service.get_hot_memory_tags_by_user(
            end_user_id=end_user_id,
            limit=limit
        )
        return success(data=result, msg="获取热门记忆标签成功")
    except Exception as e:
        api_logger.error(f"Hot memory tags by user failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取热门记忆标签失败", str(e))


@router.get("/analytics/user_profile", response_model=ApiResponse)
async def get_user_profile_api(
    end_user_id: Optional[str] = Query(None, description="用户ID（可选）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取用户详情，包含：
    - name: 用户名字（直接使用 end_user_id）
    - tags: 3个用户特征标签（从语句和实体中LLM总结）
    - hot_tags: 4个热门记忆标签
    
    返回格式：
    {
        "name": "用户名",
        "tags": ["产品设计师", "旅行爱好者", "摄影发烧友"],
        "hot_tags": [
            {"name": "标签1", "frequency": 10},
            {"name": "标签2", "frequency": 8},
            ...
        ]
    }
    """
    api_logger.info(f"User profile requested: end_user_id={end_user_id}, current_user={current_user.id}")
    try:
        result = await memory_agent_service.get_user_profile(
            end_user_id=end_user_id,
            current_user_id=str(current_user.id),
            db=db
        )
        return success(data=result, msg="获取用户详情成功")
    except Exception as e:
        api_logger.error(f"User profile failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取用户详情失败", str(e))


# @router.get("/docs/api", response_model=ApiResponse)
# async def get_api_docs_api(
#     file_path: Optional[str] = Query(None, description="API文档文件路径，不传则使用默认路径")
# ):
#     """
#     Get parsed API documentation (Public endpoint - no authentication required)
    
#     Args:
#         file_path: Optional path to API docs file. If None, uses default path.
    
#     Returns:
#         Parsed API documentation including title, meta info, and sections
#     """
#     api_logger.info(f"API docs requested, file_path: {file_path or 'default'}")
#     try:
#         result = await memory_agent_service.get_api_docs(file_path)
        
#         if result.get("success"):
#             return success(msg=result["msg"], data=result["data"])
#         else:
#             return fail(
#                 code=BizCode.BAD_REQUEST,
#                 msg=result["msg"],
#                 error=result.get("data", {}).get("error", result.get("error_code", ""))
#             )
#     except Exception as e:
#         api_logger.error(f"API docs retrieval failed: {str(e)}")
#         return fail(BizCode.INTERNAL_ERROR, "API文档获取失败", str(e))


@router.get("/end_user/{end_user_id}/connected_config", response_model=ApiResponse)
async def get_end_user_connected_config(
    end_user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取终端用户关联的记忆配置
    
    通过以下流程获取配置：
    1. 根据 end_user_id 获取用户的 app_id
    2. 获取该应用的最新发布版本
    3. 从发布版本的 config 字段中提取 memory_config_id
    
    Args:
        end_user_id: 终端用户ID
    
    Returns:
        包含 memory_config_id 和相关信息的响应
    """
    from app.services.memory_agent_service import (
        get_end_user_connected_config as get_config,
    )
    
    api_logger.info(f"Getting connected config for end_user: {end_user_id}")
    
    try:
        result = get_config(end_user_id, db)
        return success(data=result, msg="获取终端用户关联配置成功")
    except ValueError as e:
        api_logger.warning(f"End user config not found: {str(e)}")
        return fail(BizCode.NOT_FOUND, str(e))
    except Exception as e:
        api_logger.error(f"Failed to get end user connected config: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "获取终端用户关联配置失败", str(e))